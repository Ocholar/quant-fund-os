from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8-sig")

marker = "QFOS_DURABLE_SELL_IDEMPOTENCY_V1"
if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

if "QFOS_CANONICAL_TRADE_LIFECYCLE_V1" not in src:
    raise SystemExit("PATCH_FAILED: canonical lifecycle wrapper marker not found")

# A) Persist lifecycle_key in the atomic insert mapper.
start = src.find("def _qfos_insert_trade_atomic")
if start < 0:
    raise SystemExit("PATCH_FAILED: _qfos_insert_trade_atomic not found")

end_candidates = [
    p for p in (
        src.find("\ndef ", start + 10),
        src.find("\nclass ", start + 10),
    ) if p > start
]
end = min(end_candidates) if end_candidates else start + 20000
insert_block = src[start:end]

if '"lifecycle_key": normalized_fill.get("lifecycle_key")' not in insert_block:
    source_line = '"source": normalized_fill.get("source", "unknown"),'
    if source_line not in insert_block:
        raise SystemExit("PATCH_FAILED: atomic insert source mapping anchor not found")

    insert_block = insert_block.replace(
        source_line,
        source_line + '\n        "lifecycle_key": normalized_fill.get("lifecycle_key"),',
        1,
    )
    src = src[:start] + insert_block + src[end:]
    print("ATOMIC_INSERT_LIFECYCLE_KEY_MAPPING_ADDED")
else:
    print("ATOMIC_INSERT_LIFECYCLE_KEY_MAPPING_ALREADY_PRESENT")

# B) Replace wrapper's non-durable generic key with a current-open-lot SELL key.
old_key = '''    lifecycle_key = (
        f"{symbol}|{side}|{round(qty, 10)}|"
        f"{normalized.get('exit_reason') or strategy}|{source}"
    )
    normalized["lifecycle_key"] = lifecycle_key
'''

new_key = '''    # QFOS_DURABLE_SELL_IDEMPOTENCY_V1
    # BUYs remain nullable: a symbol may be bought again later.
    # SELLs are keyed to the current latest BUY lot plus reason/source/qty.
    # A retry for the same open lot therefore reaches the database unique index
    # and cannot create a second durable SELL row.
    lifecycle_key = None
    latest_open_buy_id = None

    if side == "sell":
        try:
            latest_buy = conn.execute(text("""
                SELECT id
                FROM trades
                WHERE symbol = :symbol
                  AND lower(side) = 'buy'
                ORDER BY id DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            latest_open_buy_id = int((latest_buy or {}).get("id") or 0)
        except Exception:
            latest_open_buy_id = 0

        lifecycle_key = (
            f"SELL|{symbol}|lot={latest_open_buy_id}|"
            f"qty={round(qty, 10)}|"
            f"reason={normalized.get('exit_reason') or strategy}|"
            f"source={source}"
        )

    normalized["lifecycle_key"] = lifecycle_key
'''

if old_key not in src:
    raise SystemExit("PATCH_FAILED: canonical wrapper lifecycle_key block not found")

src = src.replace(old_key, new_key, 1)

# C) Convert duplicate-key database exception into a clear reject.
old_core_call = '''    try:
        result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized, source=source)
    except TypeError:
        result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized)
'''

new_core_call = '''    try:
        try:
            result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized, source=source)
        except TypeError:
            result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized)
    except Exception as exc:
        err = str(exc).lower()
        reject_reason = (
            "duplicate_lifecycle_key"
            if "lifecycle_key" in err or "qfos_trades_sell_lifecycle_key_uq" in err
            else f"atomic_core_exception:{exc}"
        )
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol} side={side} "
            f"reason={reject_reason}",
            flush=True,
        )
        return False
'''

if old_core_call not in src:
    raise SystemExit("PATCH_FAILED: canonical atomic core call block not found")

src = src.replace(old_core_call, new_core_call, 1)

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
