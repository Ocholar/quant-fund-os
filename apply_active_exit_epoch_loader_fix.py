from pathlib import Path
import ast

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")

marker = "# QFOS_ACTIVE_EXIT_EPOCH_LOADER_V1"

if marker in src:
    print("ACTIVE_EXIT_EPOCH_LOADER_FIX_ALREADY_PRESENT")
    raise SystemExit(0)

tree = ast.parse(src)
lines = src.splitlines(keepends=True)

def find_function(name):
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"PATCH_FAILED: expected one {name} definition, found {len(matches)}."
        )
    return matches[0]

loader = find_function("_qfos_exit_open_positions_from_db")
decision = find_function("_qfos_exit_decision_for_position")

loader_replacement = r'''def _qfos_exit_open_positions_from_db():
    # QFOS_ACTIVE_EXIT_EPOCH_LOADER_V1
    # The lifecycle must evaluate the current net-open epoch only.
    # Historical closed runs in the same symbol must not contribute age,
    # weighted entry price, or lifecycle state to a new entry.
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                WITH ordered AS (
                    SELECT
                        id,
                        symbol,
                        created_at,
                        LOWER(side) AS side,
                        quantity,
                        fill_price,
                        SUM(
                            CASE
                                WHEN LOWER(side) = 'buy' THEN quantity
                                ELSE -quantity
                            END
                        ) OVER (
                            PARTITION BY symbol
                            ORDER BY id
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        ) AS running_qty
                    FROM trades
                ),
                last_flat AS (
                    SELECT
                        symbol,
                        MAX(id) FILTER (
                            WHERE running_qty <= 0.00000001
                        ) AS last_flat_id
                    FROM ordered
                    GROUP BY symbol
                ),
                current_epoch AS (
                    SELECT
                        o.symbol,
                        MIN(o.created_at) FILTER (
                            WHERE o.id > COALESCE(f.last_flat_id, 0)
                              AND o.side = 'buy'
                        ) AS entry_started_at,
                        SUM(
                            CASE
                                WHEN o.id > COALESCE(f.last_flat_id, 0)
                                THEN CASE
                                    WHEN o.side = 'buy' THEN o.quantity
                                    ELSE -o.quantity
                                END
                                ELSE 0
                            END
                        ) AS epoch_net_qty,
                        SUM(
                            CASE
                                WHEN o.id > COALESCE(f.last_flat_id, 0)
                                  AND o.side = 'buy'
                                THEN o.quantity
                                ELSE 0
                            END
                        ) AS epoch_buy_qty,
                        SUM(
                            CASE
                                WHEN o.id > COALESCE(f.last_flat_id, 0)
                                  AND o.side = 'buy'
                                THEN o.quantity * o.fill_price
                                ELSE 0
                            END
                        ) AS epoch_buy_cost
                    FROM ordered o
                    LEFT JOIN last_flat f
                        ON f.symbol = o.symbol
                    GROUP BY o.symbol
                )
                SELECT
                    p.symbol,
                    p.quantity,
                    (
                        e.epoch_buy_cost
                        / NULLIF(e.epoch_buy_qty, 0)
                    ) AS avg_entry,
                    p.last_price,
                    p.exposure,
                    p.unrealized_pnl,
                    p.strategy,
                    e.entry_started_at,
                    EXTRACT(
                        EPOCH FROM (
                            CURRENT_TIMESTAMP - e.entry_started_at
                        )
                    ) / 60.0 AS age_minutes
                FROM positions p
                JOIN current_epoch e
                    ON e.symbol = p.symbol
                WHERE p.quantity > 0.00000001
                  AND e.epoch_net_qty > 0.00000001
                  AND e.entry_started_at IS NOT NULL
                ORDER BY age_minutes DESC
            """)).mappings().all()

        return [dict(row) for row in rows]

    except Exception as exc:
        print(
            f"[EXIT_DECISION_ERROR] active_epoch_loader_error={exc!r}",
            flush=True,
        )
        return []
'''

loader_start = loader.lineno - 1
loader_end = loader.end_lineno
lines[loader_start:loader_end] = [loader_replacement + "\n"]
patched = "".join(lines)

old_peak = """    pnl_pct = (last_price - avg_entry) / avg_entry
    peak_pnl_pct = max(_qfos_exit_peak_pct.get(symbol, pnl_pct), pnl_pct)
    _qfos_exit_peak_pct[symbol] = peak_pnl_pct
"""

new_peak = """    pnl_pct = (last_price - avg_entry) / avg_entry

    # QFOS_ACTIVE_EXIT_EPOCH_LOADER_V1
    # Lifecycle peak belongs to the current net-open epoch. Reset it whenever
    # the loader identifies a new entry_started_at value for this symbol.
    try:
        _qfos_peak_epochs = globals().setdefault("_qfos_exit_peak_epochs", {})
        _qfos_entry_epoch = str(pos.get("entry_started_at") or "")
        _qfos_prior_epoch = _qfos_peak_epochs.get(symbol)

        if _qfos_entry_epoch and _qfos_prior_epoch != _qfos_entry_epoch:
            _qfos_exit_peak_pct[symbol] = float(pnl_pct)
            _qfos_peak_epochs[symbol] = _qfos_entry_epoch
            print(
                f"[EXIT_ACTIVE_EPOCH_RESET] "
                f"symbol={symbol} "
                f"entry_started_at={_qfos_entry_epoch}",
                flush=True,
            )

        peak_pnl_pct = max(
            float(_qfos_exit_peak_pct.get(symbol, pnl_pct)),
            float(pnl_pct),
        )
        _qfos_exit_peak_pct[symbol] = peak_pnl_pct

    except Exception as _qfos_peak_epoch_error:
        peak_pnl_pct = float(pnl_pct)
        _qfos_exit_peak_pct[symbol] = peak_pnl_pct
        print(
            f"[EXIT_ACTIVE_EPOCH_RESET_ERROR] "
            f"symbol={symbol} error={_qfos_peak_epoch_error!r}",
            flush=True,
        )
"""

if patched.count(old_peak) != 1:
    raise SystemExit(
        "PATCH_FAILED: expected exactly one lifecycle peak block, "
        f"found {patched.count(old_peak)}."
    )

patched = patched.replace(old_peak, new_peak, 1)

try:
    ast.parse(patched)
except SyntaxError as exc:
    raise SystemExit(f"PATCH_FAILED: patched main.py would not parse: {exc}")

path.write_bytes(patched.encode("utf-8"))
print(
    "ACTIVE_EXIT_EPOCH_LOADER_FIX_PATCH_OK "
    f"loader_line={loader.lineno} "
    f"decision_line={decision.lineno}"
)