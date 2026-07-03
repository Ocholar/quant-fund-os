import json
from datetime import datetime, timezone

from core.config import settings

try:
    import redis
except Exception:
    redis = None


_PAUSED_FALLBACK = True
_REASON_FALLBACK = "startup_interlock_no_control_state"


def _redis_client():
    if redis is None:
        return None
    try:
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


def pause_bot(reason: str = "manual_pause"):
    global _PAUSED_FALLBACK, _REASON_FALLBACK

    payload = {
        "paused": True,
        "reason": reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client = _redis_client()
    if client:
        client.set("quantfund:control", json.dumps(payload))
    else:
        _PAUSED_FALLBACK = True
        _REASON_FALLBACK = reason


def resume_bot():
    global _PAUSED_FALLBACK, _REASON_FALLBACK

    payload = {
        "paused": False,
        "reason": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client = _redis_client()
    if client:
        client.set("quantfund:control", json.dumps(payload))
    else:
        _PAUSED_FALLBACK = False
        _REASON_FALLBACK = ""


def get_control_state():
    client = _redis_client()

    if client:
        raw = client.get("quantfund:control")
        if raw:
            try:
                data = json.loads(raw)
                return {
                    "paused": bool(data.get("paused")),
                    "reason": data.get("reason") or "",
                    "updated_at": data.get("updated_at"),
                }
            except Exception:
                pass

    return {
        "paused": _PAUSED_FALLBACK,
        "reason": _REASON_FALLBACK,
        "updated_at": None,
    }


def is_paused():
    return get_control_state()["paused"]


def pause_reason():
    return get_control_state()["reason"]