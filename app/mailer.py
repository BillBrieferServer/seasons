"""Email notification for public contact-form inquiries.

Env (all optional -- if unset, submissions are still stored, just not emailed):
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS   IONOS credentials
  FROM_EMAIL                                       envelope sender
  NOTIFY_EMAIL                                     where inquiries go
"""
import os
import smtplib
import traceback
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
FROM_EMAIL = os.environ.get("FROM_EMAIL")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")


def configured() -> bool:
    return all([SMTP_HOST, SMTP_USER, SMTP_PASS, FROM_EMAIL, NOTIFY_EMAIL])


def send_inquiry(row: dict) -> bool:
    """Email one inquiry. Never raises -- the submission is already saved."""
    if not configured():
        print("[contact] email not configured; inquiry stored only")
        return False
    try:
        body = (
            "New inquiry from the Seasons Care Services website.\n\n"
            "Name:         %(name)s\n"
            "Phone:        %(phone)s\n"
            "Email:        %(email)s\n"
            "Care for:     %(care_for)s\n"
            "Best time:    %(best_time)s\n\n"
            "What would help most right now:\n%(message)s\n"
        ) % row

        msg = EmailMessage()
        msg["Subject"] = "Website inquiry - %s" % (row.get("name") or "no name")
        msg["From"] = FROM_EMAIL
        msg["To"] = NOTIFY_EMAIL
        if row.get("email"):
            msg["Reply-To"] = row["email"]
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print("[contact] inquiry emailed to %s" % NOTIFY_EMAIL)
        return True
    except Exception:
        # A mail failure must never cost us the lead or break the thank-you page.
        print("[contact] EMAIL FAILED (inquiry is saved in the database):")
        traceback.print_exc()
        return False
