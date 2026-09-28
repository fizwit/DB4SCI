#!/usr/bin/env python3
import sys
import smtplib
from smtplib import SMTPRecipientsRefused
from . import mydb_config


def send_mail(subject, message, to):
    """send email """
    SERVER = mydb_config.MAIL_SERVER
    FROM = mydb_config.MAIL_FROM
    TO = ", ".join(to)
    message = f"""\
From: {mydb_config.MAIL_FROM}
To: {TO}
Subject: {subject}

{message}
"""

    server = smtplib.SMTP(SERVER)
    try:
        server.sendmail(FROM, TO, message)
    except SMTPRecipientsRefused as e:
        return 'user unknown'
    server.quit()
    return None

if __name__ == "__main__":
    import argparse

    subject = "scicomp_srv test"
    message = 'Neither snow nor rain nor heat nor gloom of night stays '
    message += 'this mail agent from the swift completion of its appointed rounds'

    parser = argparse.ArgumentParser(description='send_mail.py unit test')
    parser.add_argument("--mail-to", type=str, required=True)
    args = parser.parse_args()

    addresses = []
    addresses.append(args.mail_to)
    status = send_mail(subject, message, addresses)
    if status:
        print(f'mail not sent to {addresses} reason: {status} ')
