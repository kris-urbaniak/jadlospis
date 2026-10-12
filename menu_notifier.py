#!/usr/bin/env python3
"""Fetch tomorrow's kindergarten menu (P385 Ursynów) and send it via email and/or WhatsApp."""

import argparse
import os
import re
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://p385.ursynow.warszawa.pl/jadlospis/"
TZ = ZoneInfo("Europe/Warsaw")
DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
COLUMNS = ["🥣 Śniadanie i II śniadanie", "🍲 Obiad", "🍪 Podwieczorek"]


def load_env(path: str = ".env") -> None:
    """Minimal .env loader (no extra dependency)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch_menu(target: date) -> dict | None:
    resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = soup.find_all("tr")
    for i, row in enumerate(rows):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        m = DATE_RE.search(cells[0].get_text(" "))
        if not m:
            continue
        d, mth, y = map(int, m.groups())
        try:
            if date(y, mth, d) != target:
                continue
        except ValueError:
            continue

        meals = [clean(c.get_text(" ")) for c in cells[1:4]]
        allergens = None
        if i + 1 < len(rows):
            nxt = rows[i + 1].find_all(["td", "th"])
            if nxt and "alergen" in nxt[0].get_text().lower():
                allergens = [clean(c.get_text(" ")) for c in nxt[1:4]]
        return {"day": clean(cells[0].get_text(" ")), "meals": meals, "allergens": allergens}
    return None


def format_message(menu: dict) -> str:
    lines = [f"Jadłospis – {menu['day']}", ""]
    for idx, (title, meal) in enumerate(zip(COLUMNS, menu["meals"])):
        lines.append(f"{title}:")
        lines.append(meal)
        if menu["allergens"] and idx < len(menu["allergens"]):
            lines.append(f"Alergeny: {menu['allergens'][idx]}")
        lines.append("")
    return "\n".join(lines).strip()


def send_email(subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    to = os.environ["EMAIL_TO"]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM", user)
    msg["To"] = to
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.sendmail(msg["From"], [a.strip() for a in to.split(",")], msg.as_string())
    print("Email sent.")


def send_whatsapp(body: str) -> None:
    """Uses the free CallMeBot API: https://www.callmebot.com/blog/free-api-whatsapp-messages/"""
    phone = os.environ["WHATSAPP_PHONE"].strip()  # e.g. +48123456789
    apikey = os.environ["CALLMEBOT_APIKEY"].strip()
    url = (
        "https://api.callmebot.com/whatsapp.php"
        f"?phone={quote_plus(phone)}&text={quote_plus(body)}&apikey={quote_plus(apikey)}"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    text = clean(re.sub(r"<[^>]+>", " ", r.text))
    # CallMeBot echoes the phone + message first, then the status; drop the
    # echoed message so the actual status/error is visible.
    status = text.replace(clean(body), "").strip()
    # CallMeBot returns HTTP 200 even on errors, so inspect the body.
    low = status.lower()
    if "queued" not in low and "sent" not in low:
        raise RuntimeError(f"CallMeBot response: {status[-500:]}")
    print(f"WhatsApp: {status[-200:]}")


def main() -> int:
    load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    p = argparse.ArgumentParser()
    p.add_argument("--date", help="Target date YYYY-MM-DD (default: tomorrow)")
    p.add_argument("--dry-run", action="store_true", help="Print only, don't send")
    p.add_argument("--only-at-hour", type=int,
                   help="Exit unless current Warsaw hour equals this (for UTC cron / DST)")
    args = p.parse_args()

    now = datetime.now(TZ)
    if args.only_at_hour is not None and now.hour != args.only_at_hour:
        print(f"Skipping: Warsaw time is {now:%H:%M}.")
        return 0

    target = date.fromisoformat(args.date) if args.date else now.date() + timedelta(days=1)

    if target.weekday() >= 5 and not args.date:
        print(f"{target} is a weekend – nothing to send.")
        return 0

    menu = fetch_menu(target)
    if not menu:
        body = f"Brak jadłospisu na {target:%d.%m.%Y} na stronie {URL}"
        subject = f"Jadłospis {target:%d.%m.%Y} – brak danych"
    else:
        body = format_message(menu)
        subject = f"Jadłospis na jutro – {menu['day']}"

    print(body)
    if args.dry_run:
        return 0

    channels = [c.strip().lower() for c in (os.environ.get("CHANNELS") or "email").split(",") if c.strip()]
    errors = 0
    for ch in channels:
        try:
            if ch == "email":
                send_email(subject, body)
            elif ch == "whatsapp":
                send_whatsapp(body)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"Failed to send via {ch}: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
