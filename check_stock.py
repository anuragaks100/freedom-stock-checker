"""
Stock checker for the Freedom Mobile Moto G Power 2025 page.

- Loads the page in a headless browser (the stock info is rendered by JavaScript)
- Reads the text under the "Availability" heading
- Sends a push (ntfy) AND an email when the item changes to IN STOCK
- Remembers the last status in state.json so you only get alerted on changes

Usage:
    python check_stock.py                # normal check
    python check_stock.py --test-notify  # just send a test push + email
"""
import json
import os
import re
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = (
    "https://shop.freedommobile.ca/en-CA/devices/Motorola/Moto-G-Power-2025"
    "?sku=840023277344&planSku=Total+Freedom+100GB+%2B+Roam+Beyond+10GB&deviceOption=MyTab"
)
ITEM_NAME = "Motorola Moto G Power 2025 (Freedom Mobile)"
STATE_FILE = Path("state.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ---------- Reading the page ----------
def classify(text: str) -> str:
    low = text.lower()
    if "out of stock" in low or "sold out" in low or "unavailable" in low:
        return "OUT"
    if "in stock" in low or "available" in low or "in stock online" in low:
        return "IN"
    return "UNKNOWN"


def get_status(page):
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    # Wait until the "Availability" heading has been rendered by JavaScript
    page.get_by_text(re.compile(r"^\s*availability\s*$", re.I)).first.wait_for(timeout=45_000)
    page.wait_for_timeout(1_500)

    lines = [l.strip() for l in page.locator("body").inner_text().splitlines() if l.strip()]
    availability_text = ""
    for i, line in enumerate(lines):
        if line.lower() == "availability" and i + 1 < len(lines):
            availability_text = lines[i + 1]
            break
    return classify(availability_text), availability_text


# ---------- Notifications ----------
def send_push(title: str, message: str):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("Push skipped: NTFY_TOPIC not set")
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": "shopping_cart",
                "Click": URL,
            },
        )
        urllib.request.urlopen(req, timeout=20)
        print("Push sent")
    except Exception as e:
        print(f"Push failed: {e}")


def send_email(subject: str, body: str):
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to = os.environ.get("EMAIL_TO") or user
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    if not (user and password and to):
        print("Email skipped: SMTP_USER / SMTP_PASS / EMAIL_TO not set")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.send_message(msg)
        print("Email sent")
    except Exception as e:
        print(f"Email failed: {e}")


def notify(title: str, message: str):
    send_push(title, message)
    send_email(title, message)


# ---------- State ----------
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(status: str):
    STATE_FILE.write_text(
        json.dumps(
            {"status": status, "updated": datetime.now(timezone.utc).isoformat()}, indent=2
        )
        + "\n"
    )


# ---------- Main ----------
def main() -> int:
    if "--test-notify" in sys.argv:
        notify("Stock checker test", f"Test alert for {ITEM_NAME}. Push and email are working.\n{URL}")
        return 0

    state = load_state()
    previous = state.get("status")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT, locale="en-CA", viewport={"width": 1366, "height": 900}
        )
        page = context.new_page()
        try:
            status, raw_text = get_status(page)
        except Exception as e:
            print(f"ERROR loading page: {e}")
            try:
                page.screenshot(path="debug.png", full_page=True)
            except Exception:
                pass
            browser.close()
            return 1

        if status == "UNKNOWN":
            print(f"Could not interpret availability text: {raw_text!r}")
            page.screenshot(path="debug.png", full_page=True)
            browser.close()
            return 1
        browser.close()

    print(f"Availability text: {raw_text!r} -> {status} (previous: {previous})")

    if status == "IN" and previous != "IN":
        notify(
            f"IN STOCK: {ITEM_NAME}",
            f"{ITEM_NAME} now shows: {raw_text}\n\nBuy it here:\n{URL}",
        )

    if status != previous:
        save_state(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
