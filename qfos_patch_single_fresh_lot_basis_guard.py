from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "QFOS_AGENT5_SINGLE_FRESH_LOT_BASIS_GUARD_V1"
if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

anchor = "def qfos_exit_lifecycle_evaluate_once(source=\"cycle\"):"
if anchor not in text:
    raise SystemExit("ERROR: lifecycle evaluation anchor not found")

helper = r'''
# ============================================================
# QFOS_AGENT5_SINGLE_FRESH_LOT_BASIS_GUARD_V1
#
# Reconciles only a provable single fresh open lot:
# - latest BUY is after latest SELL
# - open DB quantity matches that latest BUY quantity
# - stored average entry differs from latest BUY fill price
#
# This prevents lifecycle decisions using stale position avg_entry while
# the atomic firewall uses the actual fresh BUY price.
# No cash changes. No sell is created. No threshold is changed.
# ============================================================

def qfos_reconcile_single_fresh_open_lot_basis():
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT
                    p.symbol,
                    p.quantity AS open_qty,
                    p.avg_entry AS old_avg_entry,
                    p.last_price,
                    lb.id AS latest_buy_id,
                    lb.quantity AS latest_buy_qty,
                    lb.fill_price AS latest_buy_price,
                    ls.id AS latest_sell_id
                FROM positions p
                JOIN LATERAL (
                    SELECT id, quantity, fill_price
                    FROM trades
                    WHERE symbol = p.symbol
                      AND lower(side) = 'buy'
                    ORDER BY id DESC
                    LIMIT 1
                ) lb ON true
                LEFT JOIN LATERAL (
                    SELECT id
                    FROM trades
                    WHERE symbol = p.symbol
                      AND lower(side) = 'sell'
                    ORDER BY id DESC
                    LIMIT 1
                ) ls ON true
                WHERE p.quantity > 0.00000001
                  AND (ls.id IS NULL OR lb.id > ls.id)
                  AND abs(p.quantity - lb.quantity)
                      <= greatest(0.00000001, abs(lb.quantity) * 0.00001)
                  AND abs(p.avg_entry - lb.fill_price) > 0.00000001
            """)).mappings().all()

            for row in rows:
                symbol = str(row.get("symbol") or "")
                old_avg = float(row.get("old_avg_entry") or 0.0)
                new_avg = float(row.get("latest_buy_price") or 0.0)
                qty = float(row.get("open_qty") or 0.0)
                last = float(row.get("last_price") or new_avg)

                if not symbol or qty <= 0 or new_avg <= 0:
                    continue

                conn.execute(text("""
                    UPDATE positions
                    SET
                        avg_entry = :avg_entry,
                        exposure = quantity * :last_price,
                        unrealized_pnl = (:last_price - :avg_entry) * quantity,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = :symbol
                """), {
                    "symbol": symbol,
                    "avg_entry": new_avg,
                    "last_price": last,
                })

                print(
                    "[POSITION_BASIS_REPAIRED] "
                    f"symbol={symbol} qty={qty:.12f} "
                    f"old_avg_entry={old_avg:.12f} "
                    f"new_avg_entry={new_avg:.12f} "
                    f"latest_buy_id={row.get('latest_buy_id')} "
                    f"latest_sell_id={row.get('latest_sell_id')}",
                    flush=True,
                )

    except Exception as e:
        print(f"[POSITION_BASIS_REPAIR_ERROR] error={e!r}", flush=True)

# ============================================================
# End QFOS_AGENT5_SINGLE_FRESH_LOT_BASIS_GUARD_V1
# ============================================================

'''

text = text.replace(anchor, helper + "\n" + anchor, 1)

old = '''def qfos_exit_lifecycle_evaluate_once(source="cycle"):
    qfos_exit_lifecycle_ensure_tables()'''

new = '''def qfos_exit_lifecycle_evaluate_once(source="cycle"):
    qfos_exit_lifecycle_ensure_tables()
    qfos_reconcile_single_fresh_open_lot_basis()'''

if old not in text:
    raise SystemExit("ERROR: lifecycle function opening not found")

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
