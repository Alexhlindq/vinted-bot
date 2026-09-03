"""
Vinted-bevakningsbot
=====================

Vad den gör:
- Söker på Vinted enligt dina preferenser i config.json (var 5:e minut som standard)
- Kommer ihåg vilka annonser den redan sett (seen_items.json)
- Skickar en Telegram-notis så fort ett NYTT plagg dyker upp som matchar

OBS om Vinted:
Vinted har inget officiellt öppet API. Skriptet använder Vinteds interna
sök-endpoint (samma som webbsidan använder). Det kan sluta fungera om
Vinted ändrar sin sajt, och Vinted kan blockera för aggressiv trafik –
därför finns en fördröjning mellan varje sökning (poll_interval_seconds).

Installation:
    pip install requests

Körning:
    python vinted_bot.py

Konfiguration: se config.json
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
SEEN_PATH = "seen_items.json"

VINTED_SEARCH_URL = "https://www.vinted.se/api/v2/catalog/items"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# En delad session håller koll på cookies mellan anrop, precis som en webbläsare.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def ensure_vinted_session():
    """Besöker vinted.se en gång för att hämta de cookies som API:et kräver.
    Utan detta svarar Vinted med 401 Unauthorized på sök-anrop."""
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
    """Skickar ett meddelande via Telegram-boten."""
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


def search_vinted(search):
    """Gör en sökning mot Vinted och returnerar en lista med annonser."""
    params = {
        "page": 1,
        "per_page": 20,
        "order": "newest_first",
        "search_text": search.get("search_text", ""),
    }

    # Lägg endast till filter som faktiskt är ifyllda
    optional_fields = {
        "catalog_ids": "catalog_ids",
        "size_ids": "size_ids",
        "brand_ids": "brand_ids",
        "price_from": "price_from",
        "price_to": "price_to",
        "currency": "currency",
    }
    for key, param_name in optional_fields.items():
        value = search.get(key)
        if value:
            params[param_name] = value

    try:
        resp = SESSION.get(VINTED_SEARCH_URL, params=params, timeout=15)
        if resp.status_code == 401:
            # Sessionen har gått ut eller saknas – hämta en ny och försök en gång till.
            print("[Vinted] Session utgången, hämtar en ny...")
            ensure_vinted_session()
            resp = SESSION.get(VINTED_SEARCH_URL, params=params, timeout=15)

        if resp.status_code != 200:
            print(f"[Vinted] Fick statuskod {resp.status_code} för '{search.get('name')}'")
            return []
        data = resp.json()
        return data.get("items", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[Vinted] Fel vid sökning '{search.get('name')}': {e} — försöker igen om en liten stund")
        time.sleep(5)
        try:
            ensure_vinted_session()
            resp = SESSION.get(VINTED_SEARCH_URL, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("items", [])
        except (requests.RequestException, ValueError):
            pass
        return []


def format_item_message(search_name, item):
    title = item.get("title", "Okänt plagg")
    price_obj = item.get("price", {})
    price = price_obj.get("amount", "?")
    currency = price_obj.get("currency_code", "")
    brand = item.get("brand_title", "")
    size = item.get("size_title", "")
    url = item.get("url", "")

    lines = [
        f"🧥 <b>Nytt plagg – {search_name}</b>",
        f"{title}",
    ]
    if brand:
        lines.append(f"Märke: {brand}")
    if size:
        lines.append(f"Storlek: {size}")
    lines.append(f"Pris: {price} {currency}")
    if url:
        lines.append(url)
    return "\n".join(lines)


def run_once(config, seen_ids):
    telegram_cfg = config.get("telegram", {})
    bot_token = telegram_cfg.get("bot_token")
    chat_id = telegram_cfg.get("chat_id")

    new_items_found = False
    timestamp = time.strftime("%H:%M:%S")

    for search in config.get("searches", []):
        if search.get("site") != "vinted":
            continue  # Sellpy-stöd läggs till separat, se README

        items = search_vinted(search)
        print(f"[{timestamp}] '{search.get('name')}': hittade {len(items)} annonser totalt (kollar efter nya)")
        for item in items:
            item_id = str(item.get("id"))
            if not item_id or item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            new_items_found = True

            message = format_item_message(search.get("name", "Sökning"), item)
            print(f"Ny träff: {message}\n")

            if bot_token and chat_id and "DITT_" not in str(bot_token):
                send_telegram(bot_token, chat_id, message)
            else:
                print("[Telegram] bot_token/chat_id är inte ifyllda i config.json – hoppar över notis.")

    return new_items_found


def main():
    config = load_json(CONFIG_PATH, {})
    if not config:
        print(f"Hittade ingen giltig {CONFIG_PATH}. Skapa den enligt README.")
        return

    # Om Telegram-uppgifter finns som miljövariabler (t.ex. GitHub Secrets),
    # använd dem istället för det som (eventuellt) står i config.json.
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if env_token:
        config.setdefault("telegram", {})["bot_token"] = env_token
    if env_chat_id:
        config.setdefault("telegram", {})["chat_id"] = env_chat_id

    seen_ids = set(load_json(SEEN_PATH, []))
    interval = config.get("poll_interval_seconds", 300)

    print("Hämtar Vinted-session...")
    ensure_vinted_session()

    # RUN_ONCE=true (sätts av GitHub Actions) kör en enda sökrunda och avslutar,
    # eftersom schemaläggningen då sköts av GitHub istället för en oändlig loop.
    run_once_only = os.environ.get("RUN_ONCE", "").lower() == "true"

    if run_once_only:
        run_once(config, seen_ids)
        save_json(SEEN_PATH, list(seen_ids))
        print("Klar med en sökrunda (RUN_ONCE).")
        return

    print("Vinted-boten är igång. Tryck Ctrl+C för att avsluta.")
    while True:
        try:
            run_once(config, seen_ids)
            save_json(SEEN_PATH, list(seen_ids))
        except Exception as e:
            print(f"Oväntat fel: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
