#!/usr/bin/env python3

import re
import sys
import signal
import json
import argparse
import configparser

from time import sleep
from datetime import datetime

import colorlog

from curl_cffi import requests

from selenium import webdriver
from selenium.webdriver import ChromeService as Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import NoSuchElementException

from fake_useragent import UserAgent

from email_alert import email_alert

CGREEN = "\33[32m"
CBLUE = "\33[34m"
CVIOLET = "\33[35m"
CEND = "\x1b[0m"

DELAY = 600

argparser = argparse.ArgumentParser()

# python -m doctolib_scraper https://www.doctolib.fr/dermatologue/paris -i -e
argparser.add_argument("url", type=str, help="Doctolib URL to scrape")
argparser.add_argument("-d", "--delay", type=int, help="Time to wait between requests (default: 600 seconds)")
argparser.add_argument("-i", "--imminent", action="store_true", help="Show only imminent appointments (next 7 days) (default: False)")
argparser.add_argument("-e", "--email", action="store_true", help="Send an email if imminent appointment has been found (default: False)")
argparser.add_argument("-l", "--loglevel", type=str, default="warning", help="Provide logging level")

args = argparser.parse_args()

if args.delay:
    DELAY = args.delay

if args.email and args.imminent is False:
    argparser.error("--email (-e) requires --imminent (-i)")

config = configparser.ConfigParser()
config.read("config/config.ini")

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter("%(log_color)s[%(levelname)s]: %(message)s"))
logger = colorlog.getLogger(__name__)
logger.setLevel(args.loglevel.upper())
logger.addHandler(handler)

current_date = datetime.now().strftime("%Y-%m-%d")
blocked_doctor_ids = config.get("main", "blocked_doctor_ids")

def signal_handler(signal, frame) -> None:
    print("\nEnding program…")
    sys.exit(0)

def log_filter(log_) -> bool:
    # https://stackoverflow.com/questions/74546174/how-can-i-make-selenium-to-parse-every-network-request
    return (
        # is an actual response
        log_["method"] == "Network.responseReceived"
        # and JSON
        and "json" in log_["params"]["response"]["mimeType"]
        # and search_results in JSON URL
        and "search_results" in log_["params"]["response"]["url"]
    )

def get_doctor_name(driver: WebDriver, doctor_id: int) -> str | None:
    try:
        return driver.find_element(
            By.XPATH,
            f"//div[@id='search-result-{doctor_id}']//h3"
            ).get_attribute("innerHTML")

    except NoSuchElementException:
        logger.error(f"Doctor's name not found!")
        return None

def check_imminent_slots(imminent_slots: list[tuple]) -> None:
    nb_of_slots = len(imminent_slots)
    if nb_of_slots > 0:
        message = "The following imminent appointments are available:\n\n"
        for tuple in imminent_slots:
            if len(tuple) == 3:
                message += f"- {tuple[0][0]} @ {tuple[1][0]} with {tuple[2][0]}\n"
            elif len(tuple) == 2:
                message += f"- {tuple[0][0]} @ {tuple[1][0]}\n"

        if nb_of_slots == 1:
            email_alert("Found 1 imminent appointment on Doctolib", message)
        else:
            email_alert(f"Found {nb_of_slots} imminent appointments on Doctolib",
                        message)

def main() -> None:
    doctor_info = []
    invalid_doctor_ids = []
    imminent_slots = []

    logger.info("Initializing driver…")
    ua = UserAgent()
    user_agent = ua.random
    options = webdriver.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.add_argument(f"--user-agent={user_agent}")
    #options.add_argument("--headless")
    service = Service(executable_path="utils/chromedriver.exe")
    driver = webdriver.Chrome(options=options, service=service)

    logger.info("Starting scraping…")
    driver.get(args.url)
    sleep(5)

    if driver.find_element(By.XPATH, "//form[@id='challenge-form']"):
        logger.error("Failed to bypass Doctolib's bot detection.\n"
                     "Please try again in a couple of hours.")
        driver.quit()
        sys.exit(0)

    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")

    sleep(5)

    # extract requests from logs
    logs_raw = driver.get_log("performance")
    logs = [json.loads(lr["message"])["message"] for lr in logs_raw]

    for log in filter(log_filter, logs):
        resp_url = log["params"]["response"]["url"]
        doctor_id = re.search(r"\d+", resp_url).group()

        if doctor_id in blocked_doctor_ids:
            logger.warning(f"Skipping blocked doctor_id {doctor_id}")
        else:
            data = requests.get(resp_url).json()

            logger.debug("resp_url:", resp_url)

            if not "visit_motive_id" in data["search_result"]:
                logger.error(f"Invalid data or doctor does not take new appointments")
                invalid_doctor_ids.append(doctor_id)
            else:
                visit_motive_id = data["search_result"]["visit_motive_id"]
                agenda_id = data["search_result"]["agenda_ids"][0]
                practice_id = data["search_result"]["practice_ids"][0]

                doctor_info.append((doctor_id, f"https://www.doctolib.fr/availabilities.json?start_date={current_date}&visit_motive_ids={visit_motive_id}&agenda_ids={agenda_id}&practice_ids={practice_id}&limit=15"))

        # trying to avoid blocking...
        sleep(1)

    if len(invalid_doctor_ids) > 0:
        logger.warning("The following IDs are invalid or "
          "are associated to doctors who do not take new appointments.\n"
          "Consider adding them to blocked_doctor_ids: " + ", ".join(sorted(invalid_doctor_ids)))

    if len(doctor_info) > 0:
        for doctor_id, link in doctor_info:
            data = requests.get(link).json()
            if data["total"] > 0:
                doctor_name = get_doctor_name(driver, doctor_id)
                logger.info("Analyzing", link)
                if doctor_name:
                    print(f"{CVIOLET} {doctor_name} {CEND}".center(60, f"="))
                else:
                    print(f"{CVIOLET} [unknown] {CEND}".center(60, f"="))

                if len(data["availabilities"]) <= 0:
                    logger.error(f"No valid appointments found!")
                    break

                # get time of each availabilities
                for availability in data["availabilities"]:
                    for slot in availability["slots"]:
                        date_str, time_str = slot.split("T")
                        date_obj = datetime.fromisoformat(date_str)
                        time_obj = datetime.strptime(time_str, "%H:%M:%S.%f%z")
                        print(f"{CGREEN}Found imminent slot:{CEND} {date_obj.strftime('%A, %B %d, %Y')} at {time_obj.strftime('%H:%M')}")
                        if args.email:
                            if doctor_name:
                                imminent_slots.append(({date_obj.strftime("%A, %B %d, %Y")}, {time_obj.strftime("%H:%M")}, doctor_name))
                            else:
                                imminent_slots.append(({date_obj.strftime("%A, %B %d, %Y")}, {time_obj.strftime("%H:%M")}))

            else:
                if not args.imminent:
                    try:
                        if data["next_slot"]:
                            logger.info("Analyzing", link)
                            date_str, time_str = data["next_slot"].split("T")
                            date_obj = datetime.fromisoformat(date_str)
                            time_obj = datetime.strptime(time_str, "%H:%M:%S.%f%z")
                            print(f"{CBLUE}Found faraway slot:{CEND} {date_obj.strftime('%A, %B %d, %Y')} at {time_obj.strftime('%H:%M')}")
                    except KeyError:
                        continue
    else:
        logger.error(f"No doctors found!")

    check_imminent_slots(imminent_slots)

    logger.info("Exiting driver…")
    driver.quit()

    for i in range(DELAY, 0, -1):
        sleep(1)
        print(f"Retrying in: {i} seconds", end="\r")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    while True:
        main()