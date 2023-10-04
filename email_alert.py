import smtplib
import configparser
from email.message import EmailMessage

config = configparser.ConfigParser()
config.read("config/config.ini")

EMAIL_ADDRESS = config.get("email-config", "email_address")
EMAIL_PASSWORD = config.get("email-config", "email_password")
EMAIL_SERVER = config.get("email-config", "email_server")
EMAIL_PORT = config.get("email-config", "email_port")

def email_alert(subject: str, body: str):
    msg = EmailMessage()
    msg.set_content(body)
    msg["subject"] = subject
    msg["to"] = EMAIL_ADDRESS
    msg["from"] = EMAIL_ADDRESS

    server = smtplib.SMTP(EMAIL_SERVER, EMAIL_PORT)
    server.starttls()
    server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    server.send_message(msg)

    server.quit()