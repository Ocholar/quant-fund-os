from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

block = r'''
# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1
# Phase 1 execution boundary:
# All paper BUY/SELL fills must pass through qfos_persist_fill_atomic().
# This prevents fake SELL rows, duplicate full-position SELLs, oversells,
# negative positions, and SELL persistence when no open spot quantity exists.

from datetime import datetime as _qfos_datetime

_QFOS_EPSILON = 1e-12


def _qfos_now_utc_text():
    return _qfos_datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _qfos_table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r[1] for r in rows]


def _qfos_first_existing_column(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def _qfos_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_bool_int(value):
    return 1 if bool(value) else 0


def _qfos_log_atomic(message):
    try:
        print(message, flush=True)
    except Exception:
        pass


def _qfos_get_position_row(conn, symbol):
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        raise RuntimeError("positions table missing or unreadable")

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])

    if not qty_col:
        raise RuntimeError("positions table has no quantity column")

    select_cols = ["symbol", qty_col]
    if avg_col:
        select_cols.append(avg_col)
    if realized_col:
        select_cols.append(realized_col)

    sql = f"SELECT {', '.join(select_cols)} FROM positions WHERE symbol=? LIMIT 1"
    row = conn.execute(sql, (symbol,)).fetchone()

    if not row:
        return {
            "exists": False,
            "quantity": 0.0,
            "avg_entry": 0.0,
            "realized_pnl": 0.0,
            "columns": cols,
            "qty_col": qty_col,
            "avg_col": avg_col,
            "realized_col": realized_col,
        }

    idx = 1
    qty = _qfos_float(row[idx]); idx += 1

    avg_entry = 0.0
    realized = 0.0

    if avg_col:
        avg_entry = _qfos_float(row[idx]); idx += 1
    if realized_col:
        realized = _qfos_float(row[idx]); idx += 1

    return {
        "exists": True,
        "quantity": qty,
        "avg_entry": avg_entry,
        "realized_pnl": realized,
        "columns": cols,
        "qty_col": qty_col,
        "avg_col": avg_col,
        "realized_col": realized_col,
    }


def _qfos_upsert_position_atomic(conn, symbol, fill_price, strategy, new_qty, new_avg_entry, new_realized_pnl):
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        raise RuntimeError("positions table missing or unreadable")

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])
    unrealized_col = _qfos_first_existing_column(cols, ["unrealized_pnl", "pnl_unrealized"])
    last_price_col = _qfos_first_existing_column(cols, ["last_price", "mark_price", "price"])
    exposure_col = _qfos_first_existing_column(cols, ["exposure", "notional"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])
    updated_col = _qfos_first_existing_column(cols, ["updated_at", "created_at", "timestamp"])

    if not qty_col:
        raise RuntimeError("positions table has no quantity column")

    exists = conn.execute("SELECT 1 FROM positions WHERE symbol=? LIMIT 1", (symbol,)).fetchone() is not None

    safe_qty = float(max(new_qty, 0.0))

    values = {qty_col: safe_qty}

    if avg_col:
        values[avg_col] = float(new_avg_entry)

    if realized_col:
        values[realized_col] = float(new_realized_pnl)

    if unrealized_col:
        values[unrealized_col] = 0.0

    if last_price_col:
        values[last_price_col] = float(fill_price)

    if exposure_col:
        values[exposure_col] = float(safe_qty * fill_price)

    if strategy_col:
        values[strategy_col] = str(strategy or "unknown")

    if updated_col:
        values[updated_col] = _qfos_now_utc_text()

    if exists:
        assignments = ", ".join([f"{k}=?" for k in values.keys()])
        params = list(values.values()) + [symbol]
        conn.execute(f"UPDATE positions SET {assignments} WHERE symbol=?", params)
    else:
        insert_cols = ["symbol"] + list(values.keys())
        placeholders = ", ".join(["?"] * len(insert_cols))
        params = [symbol] + list(values.values())
        conn.execute(f"INSERT INTO positions ({', '.join(insert_cols)}) VALUES ({placeholders})", params)


def _qfos_insert_trade_atomic(conn, normalized_fill):
    cols = _qfos_table_columns(conn, "trades")
    if not cols:
        raise RuntimeError("trades table missing or unreadable")

    created_at = normalized_fill.get("created_at") or _qfos_now_utc_text()

    data = {
        "symbol": normalized_fill.get("symbol"),
        "side": normalized_fill.get("side"),
        "quantity": float(normalized_fill.get("quantity", 0.0)),
        "qty": float(normalized_fill.get("quantity", 0.0)),
        "expected_price": float(normalized_fill.get("expected_price", normalized_fill.get("fill_price", 0.0))),
        "fill_price": float(normalized_fill.get("fill_price", 0.0)),
        "price": float(normalized_fill.get("fill_price", 0.0)),
        "slippage_bps": float(normalized_fill.get("slippage_bps", 0.0)),
        "pnl": float(normalized_fill.get("pnl", 0.0)),
        "realized_pnl": float(normalized_fill.get("pnl", 0.0)),
        "strategy": normalized_fill.get("strategy", "unknown"),
        "confidence": float(normalized_fill.get("confidence", 0.0)),
        "live": _qfos_bool_int(normalized_fill.get("live", False)),
        "shadow_mode": _qfos_bool_int(normalized_fill.get("shadow_mode", False)),
        "source": normalized_fill.get("source", "unknown"),
        "created_at": created_at,
        "updated_at": created_at,
        "timestamp": created_at,
    }

    insert_cols = []
    insert_vals = []

    for col in cols:
        if col.lower() == "id":
            continue
        if col in data:
            insert_cols.append(col)
            insert_vals.append(data[col])

    if not insert_cols:
        raise RuntimeError("trades table has no compatible insert columns")

    placeholders = ", ".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO trades ({', '.join(insert_cols)}) VALUES ({placeholders})"
    conn.execute(sql, insert_vals)


def qfos_persist_fill_atomic(conn, fill, source="main_loop"):
    """
    Atomic paper fill persistence boundary.

    Invariants:
    - SELL with no open quantity is rejected before trade insert.
    - SELL requested_qty <= 0 is rejected before trade insert.
    - SELL requested_qty > open_qty is capped to open_qty.
    - Repeated full-position SELL after zero is rejected.
    - Position update succeeds before trade insert.
    - SELL realized PnL uses DB avg_entry.
    - Historical rows are not rewritten.
    """
    if conn is None:
        raise RuntimeError("qfos_persist_fill_atomic requires sqlite connection")

    if not isinstance(fill, dict):
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] reason=fill_not_dict source=%s" % source)
        return None

    symbol = str(fill.get("symbol") or "").strip()
    side = str(fill.get("side") or "").strip().lower()

    requested_qty = _qfos_float(fill.get("quantity", fill.get("qty")), 0.0)
    expected_price = _qfos_float(fill.get("expected_price", fill.get("fill_price", fill.get("price"))), 0.0)
    fill_price = _qfos_float(fill.get("fill_price", fill.get("price", expected_price)), 0.0)
    slippage_bps = _qfos_float(fill.get("slippage_bps"), 0.0)
    strategy = str(fill.get("strategy") or fill.get("reason") or "unknown")
    confidence = _qfos_float(fill.get("confidence"), 0.0)

    if not symbol:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] reason=missing_symbol source=%s" % source)
        return None

    if side not in ("buy", "sell"):
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s reason=invalid_side source=%s" % (symbol, side, source))
        return None

    if requested_qty <= _QFOS_EPSILON:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s qty=%s reason=non_positive_qty source=%s" % (symbol, side, requested_qty, source))
        return None

    if fill_price <= _QFOS_EPSILON:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s price=%s reason=non_positive_fill_price source=%s" % (symbol, side, fill_price, source))
        return None

    started_tx = False

    try:
        if not getattr(conn, "in_transaction", False):
            conn.execute("BEGIN IMMEDIATE")
            started_tx = True

        pos = _qfos_get_position_row(conn, symbol)
        existing_qty = _qfos_float(pos.get("quantity"), 0.0)
        existing_avg = _qfos_float(pos.get("avg_entry"), 0.0)
        existing_realized = _qfos_float(pos.get("realized_pnl"), 0.0)

        final_qty = requested_qty
        pnl = 0.0

        if side == "sell":
            if existing_qty <= _QFOS_EPSILON:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s open_qty=%s strategy=%s reason=no_open_position source=%s"
                    % (symbol, requested_qty, existing_qty, strategy, source)
                )
                if started_tx:
                    conn.rollback()
                return None

            if requested_qty > existing_qty + _QFOS_EPSILON:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_CAP] symbol=%s requested_qty=%s open_qty=%s strategy=%s reason=qty_gt_open_capped source=%s"
                    % (symbol, requested_qty, existing_qty, strategy, source)
                )
                final_qty = existing_qty

            if final_qty <= _QFOS_EPSILON:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s final_qty=%s strategy=%s reason=zero_final_qty source=%s"
                    % (symbol, requested_qty, final_qty, strategy, source)
                )
                if started_tx:
                    conn.rollback()
                return None

            new_qty = max(existing_qty - final_qty, 0.0)
            new_avg = existing_avg
            pnl = float(final_qty * (fill_price - existing_avg))
            new_realized = existing_realized + pnl

        else:
            old_value = existing_qty * existing_avg
            buy_value = requested_qty * fill_price
            new_qty = existing_qty + requested_qty
            new_avg = (old_value + buy_value) / new_qty if new_qty > _QFOS_EPSILON else fill_price
            new_realized = existing_realized
            final_qty = requested_qty
            pnl = 0.0

        if new_qty < -_QFOS_EPSILON:
            _qfos_log_atomic(
                "[FILL_VALIDATION_REJECT] symbol=%s side=%s requested_qty=%s open_qty=%s new_qty=%s reason=negative_position_guard source=%s"
                % (symbol, side, requested_qty, existing_qty, new_qty, source)
            )
            if started_tx:
                conn.rollback()
            return None

        normalized = dict(fill)
        normalized.update({
            "symbol": symbol,
            "side": side,
            "quantity": float(final_qty),
            "expected_price": float(expected_price if expected_price > _QFOS_EPSILON else fill_price),
            "fill_price": float(fill_price),
            "slippage_bps": float(slippage_bps),
            "pnl": float(pnl),
            "strategy": strategy,
            "confidence": float(confidence),
            "live": bool(fill.get("live", False)),
            "shadow_mode": bool(fill.get("shadow_mode", False)),
            "source": str(source),
            "created_at": fill.get("created_at") or _qfos_now_utc_text(),
        })

        _qfos_upsert_position_atomic(
            conn=conn,
            symbol=symbol,
            fill_price=fill_price,
            strategy=strategy,
            new_qty=new_qty,
            new_avg_entry=new_avg,
            new_realized_pnl=new_realized,
        )

        _qfos_insert_trade_atomic(conn, normalized)

        if started_tx:
            conn.commit()

        _qfos_log_atomic(
            "[FILL_PERSISTED_ATOMIC] symbol=%s side=%s qty=%s new_qty=%s pnl=%s strategy=%s source=%s"
            % (symbol, side, final_qty, new_qty, pnl, strategy, source)
        )

        return normalized

    except Exception as exc:
        if started_tx:
            try:
                conn.rollback()
            except Exception:
                pass
        _qfos_log_atomic(
            "[FILL_PERSISTENCE_ERROR] symbol=%s side=%s qty=%s error=%s source=%s"
            % (symbol, side, requested_qty, repr(exc), source)
        )
        raise
# END QFOS_ATOMIC_FILL_PERSISTENCE_V1
'''

pattern = re.compile(
    r"\n?# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1\n?",
    flags=re.S,
)

if pattern.search(text):
    text = pattern.sub("\n" + block + "\n", text)
    print("Replaced existing QFOS atomic persistence block.")
else:
    m = re.search(r"(?m)^def\s+main\s*\(", text)
    if m:
        text = text[:m.start()] + "\n" + block + "\n\n" + text[m.start():]
        print("Inserted QFOS atomic persistence block before def main().")
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
        print("Appended QFOS atomic persistence block at end of main.py.")

path.write_text(text, encoding="utf-8")
print("main.py patched successfully.")
