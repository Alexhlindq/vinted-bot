"""
Koll av egna annonser
======================

Vad den gör:
- Hämtar dina egna aktiva annonser från din Vinted-profil (my_listings.user_id i config.json)
- Räknar ut hur många dagar sedan varje annons lades upp
- Flaggar annonser som är äldre än my_listings.bump_after_days som "behöver puttas"
- Skriver resultatet till my_listings.json (för dashboarden)
- Skickar en Telegram-notis med listan över annonser som behöver puttas

OBS om ålder på annons:
Vinteds publika API returnerar inte alltid ett rent "publicerad datum"-fält.
Skriptet försöker i turordning: created_at_ts, created_at, updated_at_ts,
och faller sist tillbaka på bildens uppladdningstid (photo.high_resolution.timestamp)
som en approximation. Om inget av detta finns markeras annonsen som "ålder okänd"
istället för att gissa fel.

Körning: python check_my_listings.py
Konfiguration: se config.json -> "my_listings"
"""

import json
import time
import os
import builtins
import requests

def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    builtins.print(*args, **kwargs)

CONFIG_PATH = "config.json"
MY_LISTINGS_OUTPUT_PATH = "my_listings.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def ensure_vinted_session():
    """Besöker vinted.se en gång för att hämta de cookies som API:et kräver."""
    try:
        resp = SESSION.get("https://www.vinted.se/", timeout=15)
        if resp.status_code != 200:
            print(f"[Vinted] Kunde inte hämta session (status {resp.status_code})")
    except requests.RequestException as e:
        print(f"[Vinted] Fel vid hämtning av session: {e}")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[Telegram] Fel vid sändning: {resp.status_code} {resp.text}")
    except requests.RequestException as e:
        print(f"[Telegram] Nätverksfel: {e}")


def fetch_user_items(user_id):
    """Hämtar användarens publika annonser via Vinteds interna API."""
    url = f"https://www.vinted.se/api/v2/users/{user_id}/items"
    params = {"page": 1, "per_page": 100, "order": "relevance"}
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        if resp.status_code == 401:
            ensure_vinted_session()
            resp = SESSION.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"[Vinted] Fick statuskod {resp.status_code} för användare {user_id}")
            return []
        return resp.json().get("items", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[Vinted] Fel vid hämtning av annonser: {e}")
        return []


def get_listing_age_days(item):
    """Försöker hitta ett datum för när annonsen lades upp och returnerar ålder i dagar.
    Returnerar None om inget rimligt datum kunde hittas."""
    now = time.time()

    for key in ("created_at_ts", "updated_at_ts"):
        value = item.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return max(0, int((now - value) / 86400))

    for key in ("created_at", "updated_at"):
        value = item.get(key)
        if isinstance(value, str) and value:
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
                try:
                    ts = time.mktime(time.strptime(value, fmt))
                    return max(0, int((now - ts) / 86400))
                except ValueError:
                    continue

    photo = item.get("photo") or {}
    high_res = photo.get("high_resolution") or {}
    ts = high_res.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return max(0, int((now - ts) / 86400))

    return None


def build_listing_entry(item, bump_after_days):
    price_obj = item.get("price", {}) or {}
    photo = item.get("photo") or {}
    age_days = get_listing_age_days(item)

    return {
        "id": str(item.get("id", "")),
        "title": item.get("title", "Okänt plagg"),
        "price": price_obj.get("amount", "?"),
        "currency": price_obj.get("currency_code", ""),
        "url": item.get("url", ""),
        "image_url": photo.get("url", ""),
        "age_days": age_days,
        "needs_bump": (age_days is not None and age_days >= bump_after_days),
    }


def format_bump_message(listings_needing_bump):
    lines = [f"📦 <b>{len(listings_needing_bump)} annons(er) behöver puttas</b>"]
    for entry in listings_needing_bump:
        age = f"{entry['age_days']} dagar gammal" if entry["age_days"] is not None else "ålder okänd"
        lines.append(f"• {entry['title']} ({age})")
        if entry["url"]:
            lines.append(entry["url"])
    return "\n".join(lines)


def main():
    config = load_json(CONFIG_PATH, {})
    if not config:
        print(f"Hittade ingen giltig {CONFIG_PATH}.")
        return

    my_listings_cfg = config.get("my_listings", {})
    user_id = my_listings_cfg.get("user_id")
    bump_after_days = my_listings_cfg.get("bump_after_days", 14)

    if not user_id:
        print("Inget my_listings.user_id angivet i config.json — hoppar över kollen.")
        return

    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    telegram_cfg = config.get("telegram", {})
    bot_token = env_token or telegram_cfg.get("bot_token")
    chat_id = env_chat_id or telegram_cfg.get("chat_id")

    print("Hämtar Vinted-session...")
    ensure_vinted_session()

    print(f"Hämtar annonser för användare {user_id}...")
    items = fetch_user_items(user_id)
    print(f"Hittade {len(items)} annons(er) totalt.")

    listings = [build_listing_entry(item, bump_after_days) for item in items]
    listings.sort(key=lambda x: (x["age_days"] is None, -(x["age_days"] or 0)))

    save_json(
        MY_LISTINGS_OUTPUT_PATH,
        {
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bump_after_days": bump_after_days,
            "listings": listings,
        },
    )

    needing_bump = [l for l in listings if l["needs_bump"]]
    if needing_bump:
        message = format_bump_message(needing_bump)
        print(message)
        if bot_token and chat_id and "DITT_" not in str(bot_token):
            send_telegram(bot_token, chat_id, message)
        else:
            print("[Telegram] bot_token/chat_id är inte ifyllda — hoppar över notis.")
    else:
        print("Inga annonser behöver puttas just nu.")


if __name__ == "__main__":
    main()
