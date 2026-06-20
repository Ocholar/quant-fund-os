from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT5_SELL_SYMBOL_MUTEX_V1"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_AGENT5_SELL_SYMBOL_MUTEX_V1
# Purpose:
#   Prevent competing exit paths from selling the same symbol in the same cycle.
#
# Failure observed:
#   main_loop persisted a SELL, then qfos_exit_lifecycle immediately allowed
#   a second SELL for the same symbol with a different exit_reason.
#
# Rule:
#   Once any SELL is accepted for a symbol, all other SELLs for that symbol
#   are blocked briefly, regardless of strategy/exit_reason.

import time as _qfos_agent5_mutex_time

_QFOS_AGENT5_SELL_SYMBOL_MUTEX = {}

def qfos_agent5_symbol_mutex_cleanup(now=None, ttl_seconds=90):
    try:
        now = float(now or _qfos_agent5_mutex_time.time())
        stale = [
            k for k, v in list(_QFOS_AGENT5_SELL_SYMBOL_MUTEX.items())
            if now - float(v.get("ts", 0.0)) > ttl_seconds
        ]
        for k in stale:
            _QFOS_AGENT5_SELL_SYMBOL_MUTEX.pop(k, None)
    except Exception:
        pass


def qfos_agent5_symbol_mutex_check(symbol, fill=None, source="unknown"):
    try:
        now = _qfos_agent5_mutex_time.time()
        qfos_agent5_symbol_mutex_cleanup(now=now)

        symbol = str(symbol or "").strip()
        if not symbol:
            return False, "missing_symbol"

        existing = _QFOS_AGENT5_SELL_SYMBOL_MUTEX.get(symbol)
        if existing:
            age = now - float(existing.get("ts", 0.0))
            print(
                f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_symbol_mutex "
                f"age={age:.3f}s previous_source={existing.get('source')} "
                f"previous_reason={existing.get('reason')} source={source}",
                flush=True,
            )
            return False, "sell_symbol_mutex"

        return True, "sell_symbol_mutex_clear"

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_symbol_mutex_error error={exc!r} source={source}",
            flush=True,
        )
        return False, "sell_symbol_mutex_error"


def qfos_agent5_symbol_mutex_mark(symbol, fill=None, source="unknown"):
    try:
        symbol = str(symbol or "").strip()
        if not symbol:
            return

        reason = ""
        try:
            reason = str(
                (fill or {}).get("exit_reason")
                or (fill or {}).get("reason")
                or (fill or {}).get("strategy")
                or ""
            )
        except Exception:
            reason = ""

        _QFOS_AGENT5_SELL_SYMBOL_MUTEX[symbol] = {
            "ts": _qfos_agent5_mutex_time.time(),
            "source": source,
            "reason": reason,
        }

        print(
            f"[SELL_SYMBOL_MUTEX_MARK] symbol={symbol} reason={reason} source={source}",
            flush=True,
        )
    except Exception:
        pass

# END QFOS_AGENT5_SELL_SYMBOL_MUTEX_V1
'''

# Insert helper before hard sell idempotency helper if present, otherwise before atomic persistence.
anchor = "# QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1"
idx = src.find(anchor)

if idx == -1:
    anchor = "# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1"
    idx = src.find(anchor)

if idx == -1:
    anchor = "def qfos_persist_fill_atomic"
    idx = src.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: insertion anchor not found")

src = src[:idx] + helper + "\n\n" + src[idx:]


# Patch qfos_agent5_atomic_sell_guard to check and mark the mutex.
# 1) Insert mutex check after side == sell and symbol/qty/price/reason are available.
needle = "open_qty = qfos_agent5_db_open_position_qty(conn, symbol)"
if needle not in src:
    raise SystemExit("PATCH_FAILED: could not find open_qty line inside sell guard")

mutex_check = (
    "mutex_ok, mutex_reason = qfos_agent5_symbol_mutex_check(symbol, fill=fill, source=source)\n"
    "    if not mutex_ok:\n"
    "        return False, fill, mutex_reason\n\n"
    "    open_qty = qfos_agent5_db_open_position_qty(conn, symbol)"
)

src = src.replace(needle, mutex_check, 1)

# 2) Mark mutex immediately before returning allowed SELL.
needle2 = 'return True, guarded, "sell_open_qty_confirmed"'
if needle2 not in src:
    raise SystemExit("PATCH_FAILED: could not find SELL allow return line")

src = src.replace(
    needle2,
    'qfos_agent5_symbol_mutex_mark(symbol, fill=guarded, source=source)\n'
    '    return True, guarded, "sell_open_qty_confirmed"',
    1
)

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
