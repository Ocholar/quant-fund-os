import os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dotenv import load_dotenv

# Load .env from project root/current working directory.
# override=True ensures .env wins over stale shell values.
load_dotenv(override=False)

try:
    from core.config import settings
except Exception:
    settings = None


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_or_setting(env_name: str, setting_name: str, default=""):
    """
    Prefer environment variables loaded from .env.
    Fall back to core.config.settings only when env is missing/blank.
    """
    value = os.getenv(env_name)
    if value is not None and str(value).strip() != "":
        return value

    if settings is not None:
        return getattr(settings, setting_name, default)

    return default


def telegram_alerts_enabled() -> bool:
    value = _env_or_setting("ALERTS_ENABLED", "alerts_enabled", False)
    return _truthy(value)


def telegram_config_debug() -> dict:
    """
    Safe debug view. Does not print the real bot token.
    """
    token = str(_env_or_setting("TELEGRAM_BOT_TOKEN", "telegram_bot_token", "") or "").strip()
    chat_id = str(_env_or_setting("TELEGRAM_CHAT_ID", "telegram_chat_id", "") or "").strip()

    return {
        "alerts_enabled_raw": str(_env_or_setting("ALERTS_ENABLED", "alerts_enabled", "")),
        "alerts_enabled_bool": telegram_alerts_enabled(),
        "telegram_bot_token_present": bool(token),
        "telegram_bot_token_preview": token[:8] + "..." if token else "",
        "telegram_chat_id_present": bool(chat_id),
        "telegram_chat_id": chat_id,
    }


def send_telegram_alert(message: str) -> bool:
    """
    Synchronous Telegram alert sender.
    Returns True if Telegram accepted the message.
    Never raises to the trading loop.
    """
    try:
        if not telegram_alerts_enabled():
            print("TELEGRAM ALERT SKIPPED: ALERTS_ENABLED is false")
            print("TELEGRAM DEBUG:", telegram_config_debug())
            return False

        token = str(_env_or_setting("TELEGRAM_BOT_TOKEN", "telegram_bot_token", "") or "").strip()
        chat_id = str(_env_or_setting("TELEGRAM_CHAT_ID", "telegram_chat_id", "") or "").strip()

        if not token:
            print("TELEGRAM ALERT SKIPPED: TELEGRAM_BOT_TOKEN missing")
            print("TELEGRAM DEBUG:", telegram_config_debug())
            return False

        if not chat_id:
            print("TELEGRAM ALERT SKIPPED: TELEGRAM_CHAT_ID missing")
            print("TELEGRAM DEBUG:", telegram_config_debug())
            return False

        text = str(message or "").strip() or "Quant Fund OS alert"

        payload = urlencode({
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": "true",
        }).encode("utf-8")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "QuantFundOS/1.0",
            },
        )

        with urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")

        if '"ok":true' in body.lower():
            print("TELEGRAM ALERT SENT")
            return True

        print("TELEGRAM ALERT RESPONSE:", body[:500])
        return False

    except Exception as e:
        print("TELEGRAM ALERT ERROR:", repr(e))
        print("TELEGRAM DEBUG:", telegram_config_debug())
        return False


def send_startup_alert() -> bool:
    live = False
    if settings is not None:
        live = bool(getattr(settings, "live_trading", False))

    return send_telegram_alert(
        "🚀 Quant Fund OS started\n"
        f"Mode: {'LIVE' if live else 'paper'}\n"
        f"Time: {datetime.utcnow().isoformat(timespec='seconds')}Z"
    )
