import os
import requests


def alerts_enabled():
    return os.getenv("ALERTS_ENABLED", "false").lower() == "true"


def send_telegram_alert(message: str):
    if not alerts_enabled():
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return response.ok
    except Exception:
        return False
