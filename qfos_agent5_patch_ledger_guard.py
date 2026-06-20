from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

if "QFOS_AGENT5_POSTGRES_LEDGER_LINEAGE_GUARD_V1" in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''
# ============================================================
# QFOS_AGENT5_POSTGRES_LEDGER_LINEAGE_GUARD_V1
# Purpose:
#   Prevent orphan Postgres position rows from being restored into
#   executable paper runtime state unless they have trade lineage or
#   explicit PM-approved metadata.
# ============================================================

def qfos_position_has_valid_lineage_or_marker(conn, symbol, strategy=None):
    try:
        sym = str(symbol or "").strip()
        strat = str(strategy or "").strip().lower()

        if not sym:
            return False, "missing_symbol"

        approved_markers = (
            "seeded",
            "seed",
            "test",
            "reconciled",
            "approved_migration",
            "pm_approved_migration",
            "lineage_status=approved",
            "source=reconciled",
        )

        # Explicit markers must not be paper_position_sync alone.
        if any(marker in strat for marker in approved_markers):
            return True, "approved_marker"

        row = conn.execute(text("""
            SELECT
                COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END), 0) AS buy_qty,
                COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END), 0) AS sell_qty,
                COUNT(*) AS trade_rows
            FROM trades
            WHERE symbol = :symbol
        """), {"symbol": sym}).mappings().first()

        buy_qty = float((row or {}).get("buy_qty") or 0.0)
        sell_qty = float((row or {}).get("sell_qty") or 0.0)
        trade_rows = int((row or {}).get("trade_rows") or 0)
        net_qty = buy_qty - sell_qty

        if trade_rows > 0 and net_qty > 0.00000001:
            return True, "net_trade_lineage"

        return False, "no_trade_lineage"
    except Exception as e:
        print(f"[LEDGER_GUARD] lineage_check_error symbol={symbol} error={e}", flush=True)
        return False, "lineage_check_error"


def qfos_runtime_restore_position_if_valid(conn, row):
    try:
        symbol = row["symbol"]
        qty = float(row["quantity"] or 0.0)
        avg_entry = float(row["avg_entry"] or 0.0)
        strategy = row.get("strategy") if hasattr(row, "get") else row["strategy"]

        if qty <= 0:
            return False

        ok, reason = qfos_position_has_valid_lineage_or_marker(conn, symbol, strategy)

        if not ok:
            print(
                f"[LEDGER_GUARD] blocked_orphan_position_restore symbol={symbol} "
                f"qty={qty} strategy={strategy} reason={reason}",
                flush=True,
            )
            return False

        portfolio.positions[symbol] = qty
        entry_prices[symbol] = avg_entry
        print(
            f"[LEDGER_GUARD] restored_position symbol={symbol} qty={qty} reason={reason}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[LEDGER_GUARD] restore_error error={e}", flush=True)
        return False

# ============================================================
# End QFOS_AGENT5_POSTGRES_LEDGER_LINEAGE_GUARD_V1
# ============================================================
'''

# Insert helper before load_state_from_db.
marker = "def load_state_from_db():"
if marker not in text:
    raise SystemExit("ERROR: def load_state_from_db() not found")

text = text.replace(marker, helper + "\n\n" + marker, 1)

# Replace the risky SELECT and direct restore loop pattern.
old_select = """rows = conn.execute(text('\\n                SELECT symbol, quantity, avg_entry FROM positions WHERE quantity > 0\\n            ')).mappings().all()"""
new_select = """rows = conn.execute(text('''
                SELECT symbol, quantity, avg_entry, strategy
                FROM positions
                WHERE quantity > 0
            ''')).mappings().all()"""

if old_select in text:
    text = text.replace(old_select, new_select, 1)
else:
    # More flexible replacement for formatted variants.
    text = re.sub(
        r"rows\s*=\s*conn\.execute\(text\(\s*['\"]{1,3}\s*SELECT\s+symbol,\s+quantity,\s+avg_entry\s+FROM\s+positions\s+WHERE\s+quantity\s*>\s*0\s*['\"]{1,3}\s*\)\)\.mappings\(\)\.all\(\)",
        new_select,
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

old_loop = """for r in rows:
                portfolio.positions[r['symbol']] = float(r['quantity'])
                entry_prices[r['symbol']] = float(r['avg_entry'])
            if rows:
                print(f'Recovered {len(rows)} open positions.')"""

new_loop = """restored_positions = 0
            for r in rows:
                if qfos_runtime_restore_position_if_valid(conn, r):
                    restored_positions += 1
            if rows:
                print(f'Recovered {restored_positions}/{len(rows)} open positions after ledger lineage guard.')"""

if old_loop in text:
    text = text.replace(old_loop, new_loop, 1)
else:
    text = re.sub(
        r"for\s+r\s+in\s+rows:\s*\n\s*portfolio\.positions\[r\[['\"]symbol['\"]\]\]\s*=\s*float\(r\[['\"]quantity['\"]\]\)\s*\n\s*entry_prices\[r\[['\"]symbol['\"]\]\]\s*=\s*float\(r\[['\"]avg_entry['\"]\]\)\s*\n\s*if\s+rows:\s*\n\s*print\(f['\"]Recovered\s+\{len\(rows\)\}\s+open\s+positions\.['\"]\)",
        new_loop,
        text,
        count=1,
        flags=re.DOTALL,
    )

if "blocked_orphan_position_restore" not in text:
    raise SystemExit("ERROR: ledger guard insertion failed")

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
