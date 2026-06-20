from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1
# Purpose:
#   Stop duplicate SELL persistence and oversells at the single atomic boundary.
#
# Root failure:
#   Duplicate SELL rows and negative running_qty proved SELLs were persisted
#   after the position was already closed.
#
# Rule:
#   A SELL may persist only when current DB open quantity exists.
#   SELL quantity must be clamped to DB open quantity.
#   Duplicate SELL intent within a short window is rejected.

def qfos_agent5_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def qfos_agent5_sell_guard_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def qfos_agent5_sell_guard_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def qfos_agent5_sell_guard_qty(fill):
    try:
        return qfos_agent5_float((fill or {}).get("quantity", (fill or {}).get("qty", 0.0)))
    except Exception:
        return 0.0


def qfos_agent5_sell_guard_price(fill):
    try:
        return qfos_agent5_float(
            (fill or {}).get(
                "fill_price",
                (fill or {}).get("price", (fill or {}).get("expected_price", 0.0)),
            )
        )
    except Exception:
        return 0.0


def qfos_agent5_sell_guard_reason(fill):
    try:
        return str(
            (fill or {}).get("exit_reason")
            or (fill or {}).get("reason")
            or (fill or {}).get("strategy")
            or "exit"
        ).strip()
    except Exception:
        return "exit"


def qfos_agent5_db_open_position_qty(conn, symbol):
    try:
        row = conn.execute(
            text("""
                select quantity
                from positions
                where symbol=:symbol
                  and coalesce(quantity,0) > 0.00000001
                limit 1
            """),
            {"symbol": symbol},
        ).mappings().first()

        if not row:
            return 0.0

        return max(0.0, qfos_agent5_float(row.get("quantity")))

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=open_qty_lookup_error error={repr(exc)}",
            flush=True,
        )
        return 0.0


def qfos_agent5_recent_duplicate_sell(conn, fill, seconds=30):
    symbol = qfos_agent5_sell_guard_symbol(fill)
    qty = qfos_agent5_sell_guard_qty(fill)
    price = qfos_agent5_sell_guard_price(fill)
    reason = qfos_agent5_sell_guard_reason(fill)

    try:
        row = conn.execute(
            text("""
                select id, created_at
                from trades
                where lower(side)='sell'
                  and symbol=:symbol
                  and abs(quantity - :quantity) <= 0.00001
                  and abs(fill_price - :fill_price) <= 0.00001
                  and coalesce(exit_reason,'') = :exit_reason
                  and created_at >= (CURRENT_TIMESTAMP - (:seconds || ' seconds')::interval)
                order by id desc
                limit 1
            """),
            {
                "symbol": symbol,
                "quantity": qty,
                "fill_price": price,
                "exit_reason": reason,
                "seconds": str(int(seconds)),
            },
        ).mappings().first()

        return row

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=duplicate_lookup_error error={repr(exc)}",
            flush=True,
        )
        return None


def qfos_agent5_atomic_sell_guard(conn, fill, source="unknown"):
    if not isinstance(fill, dict):
        return False, fill, "fill_not_dict"

    side = qfos_agent5_sell_guard_side(fill)
    if side != "sell":
        return True, fill, "non_sell"

    symbol = qfos_agent5_sell_guard_symbol(fill)
    qty = qfos_agent5_sell_guard_qty(fill)
    price = qfos_agent5_sell_guard_price(fill)
    reason = qfos_agent5_sell_guard_reason(fill)

    if not symbol:
        print("[SELL_VALIDATION_REJECT] reason=missing_symbol source=%s" % source, flush=True)
        return False, fill, "missing_symbol"

    if qty <= 0:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=bad_sell_qty qty={qty} source={source}",
            flush=True,
        )
        return False, fill, "bad_sell_qty"

    if price <= 0:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=bad_sell_price price={price} source={source}",
            flush=True,
        )
        return False, fill, "bad_sell_price"

    open_qty = qfos_agent5_db_open_position_qty(conn, symbol)

    if open_qty <= 0.00000001:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_no_open_position "
            f"requested_qty={qty:.12f} db_open_qty={open_qty:.12f} "
            f"exit_reason={reason} source={source}",
            flush=True,
        )
        return False, fill, "sell_no_open_position"

    dup = qfos_agent5_recent_duplicate_sell(conn, fill)
    if dup:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=duplicate_sell_intent "
            f"duplicate_id={dup.get('id')} requested_qty={qty:.12f} "
            f"fill_price={price:.12f} exit_reason={reason} source={source}",
            flush=True,
        )
        return False, fill, "duplicate_sell_intent"

    guarded = dict(fill)

    # Clamp tiny float overshoot to open quantity.
    if qty > open_qty:
        if qty <= open_qty + 0.00001:
            guarded["quantity"] = open_qty
            guarded["qty"] = open_qty
            print(
                f"[SELL_VALIDATION_CLAMP] symbol={symbol} reason=float_tolerance "
                f"requested_qty={qty:.12f} db_open_qty={open_qty:.12f} "
                f"exit_reason={reason} source={source}",
                flush=True,
            )
        else:
            print(
                f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_qty_exceeds_open "
                f"requested_qty={qty:.12f} db_open_qty={open_qty:.12f} "
                f"exit_reason={reason} source={source}",
                flush=True,
            )
            return False, fill, "sell_qty_exceeds_open"

    guarded["side"] = "sell"
    guarded["symbol"] = symbol
    guarded["fill_price"] = price
    guarded["price"] = price
    guarded["is_exit"] = True
    guarded["exit_reason"] = reason
    guarded["reason"] = reason
    guarded["strategy"] = reason

    print(
        f"[SELL_VALIDATION_ALLOW] symbol={symbol} requested_qty={qty:.12f} "
        f"db_open_qty={open_qty:.12f} fill_price={price:.12f} "
        f"exit_reason={reason} source={source}",
        flush=True,
    )

    return True, guarded, "sell_open_qty_confirmed"

# END QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1
'''

# Insert helper before qfos_persist_fill_atomic block.
anchor = "# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1"
idx = src.find(anchor)

if idx == -1:
    anchor = "def qfos_persist_fill_atomic"
    idx = src.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: qfos_persist_fill_atomic anchor not found")

src = src[:idx] + helper + "\n\n" + src[idx:]


# Insert guard at the top of qfos_persist_fill_atomic after normalized fallback if present.
m = re.search(r"(def\s+qfos_persist_fill_atomic\s*\([^\)]*\):\n)", src)

if not m:
    raise SystemExit("PATCH_FAILED: qfos_persist_fill_atomic definition not found")

insert_at = m.end()

guard_code = (
    "    # QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1: enforce SELL idempotency before any persistence\n"
    "    try:\n"
    "        _qfos_sell_ok, _qfos_guarded_fill, _qfos_sell_reason = qfos_agent5_atomic_sell_guard(conn, fill, source=source)\n"
    "        if not _qfos_sell_ok:\n"
    "            return False\n"
    "        fill = _qfos_guarded_fill\n"
    "    except Exception as _qfos_sell_guard_error:\n"
    "        print(f\"[SELL_VALIDATION_REJECT] reason=sell_guard_exception error={_qfos_sell_guard_error!r} source={source}\", flush=True)\n"
    "        return False\n"
)

src = src[:insert_at] + guard_code + src[insert_at:]


path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
