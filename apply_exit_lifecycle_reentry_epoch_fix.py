from pathlib import Path
import ast
import re

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")

marker = "# QFOS_EXIT_LIFECYCLE_REENTRY_EPOCH_V1"

if marker in src:
    print("EXIT_REENTRY_EPOCH_FIX_ALREADY_PRESENT")
    raise SystemExit(0)

fetch_pattern = re.compile(
    r"def qfos_exit_lifecycle_fetch_positions\(\):.*?"
    r"(?=\ndef qfos_exit_lifecycle_current_regime\(\):)",
    re.S,
)

peak_pattern = re.compile(
    r"def qfos_exit_lifecycle_get_peak\(conn, symbol, pnl_pct\):.*?"
    r"(?=\ndef qfos_exit_lifecycle_strong_runner\()",
    re.S,
)

fetch_match = fetch_pattern.search(src)
peak_match = peak_pattern.search(src)

if not fetch_match:
    raise SystemExit("PATCH_FAILED: qfos_exit_lifecycle_fetch_positions anchor not found.")

if not peak_match:
    raise SystemExit("PATCH_FAILED: qfos_exit_lifecycle_get_peak anchor not found.")

old_call = "peak_pnl_pct = qfos_exit_lifecycle_get_peak(conn, symbol, pnl_pct)"

if src.count(old_call) != 1:
    raise SystemExit(
        "PATCH_FAILED: expected exactly one lifecycle peak call, "
        f"found {src.count(old_call)}."
    )

fetch_replacement = r'''def qfos_exit_lifecycle_fetch_positions():
    # QFOS_EXIT_LIFECYCLE_REENTRY_EPOCH_V1
    # Age must begin with the current net-open run, not the first BUY ever
    # recorded for a symbol. This prevents a closed historical position from
    # making a new entry appear immediately stale.
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                WITH ordered AS (
                    SELECT
                        id,
                        symbol,
                        created_at,
                        side,
                        quantity,
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
                current_open_epoch AS (
                    SELECT
                        o.symbol,
                        MIN(o.created_at) FILTER (
                            WHERE o.id > COALESCE(f.last_flat_id, 0)
                              AND LOWER(o.side) = 'buy'
                        ) AS entry_started_at
                    FROM ordered o
                    LEFT JOIN last_flat f
                        ON f.symbol = o.symbol
                    GROUP BY o.symbol
                )
                SELECT
                    p.symbol,
                    p.quantity,
                    p.avg_entry,
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
                JOIN current_open_epoch e
                    ON e.symbol = p.symbol
                WHERE p.quantity > 0.00000001
                  AND e.entry_started_at IS NOT NULL
                ORDER BY age_minutes DESC
            """)).mappings().all()

        return [dict(r) for r in rows]

    except Exception as e:
        print(
            f"[EXIT_DECISION_ERROR] fetch_positions error={e}",
            flush=True,
        )
        return []
'''

peak_replacement = r'''def qfos_exit_lifecycle_get_peak(
    conn,
    symbol,
    pnl_pct,
    entry_started_at=None,
):
    # QFOS_EXIT_LIFECYCLE_REENTRY_EPOCH_V1
    # Peak PnL belongs to one open position epoch only. A later re-entry must
    # not inherit a peak from an earlier, closed position in the same symbol.
    try:
        conn.execute(text("""
            ALTER TABLE qfos_exit_lifecycle_state
            ADD COLUMN IF NOT EXISTS entry_started_at TIMESTAMP
        """))
    except Exception:
        pass

    row = conn.execute(text("""
        SELECT
            peak_pnl_pct,
            entry_started_at
        FROM qfos_exit_lifecycle_state
        WHERE symbol = :symbol
    """), {
        "symbol": symbol,
    }).mappings().first()

    old_peak = float((row or {}).get("peak_pnl_pct") or pnl_pct or 0.0)
    old_epoch = (row or {}).get("entry_started_at")

    is_new_epoch = (
        entry_started_at is not None
        and old_epoch != entry_started_at
    )

    if is_new_epoch:
        peak = float(pnl_pct or 0.0)
    else:
        peak = max(old_peak, float(pnl_pct or 0.0))

    conn.execute(text("""
        INSERT INTO qfos_exit_lifecycle_state (
            symbol,
            entry_started_at,
            peak_pnl_pct,
            last_pnl_pct,
            updated_at
        )
        VALUES (
            :symbol,
            :entry_started_at,
            :peak,
            :pnl,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (symbol)
        DO UPDATE SET
            entry_started_at = EXCLUDED.entry_started_at,
            peak_pnl_pct = CASE
                WHEN qfos_exit_lifecycle_state.entry_started_at
                     IS DISTINCT FROM EXCLUDED.entry_started_at
                THEN EXCLUDED.peak_pnl_pct
                ELSE GREATEST(
                    qfos_exit_lifecycle_state.peak_pnl_pct,
                    EXCLUDED.peak_pnl_pct
                )
            END,
            last_pnl_pct = EXCLUDED.last_pnl_pct,
            updated_at = CURRENT_TIMESTAMP
    """), {
        "symbol": symbol,
        "entry_started_at": entry_started_at,
        "peak": peak,
        "pnl": float(pnl_pct or 0.0),
    })

    if is_new_epoch:
        print(
            f"[EXIT_REENTRY_EPOCH_RESET] "
            f"symbol={symbol} "
            f"entry_started_at={entry_started_at}",
            flush=True,
        )

    return peak
'''

patched = src[:fetch_match.start()] + fetch_replacement + src[fetch_match.end():]

peak_match_after_fetch = peak_pattern.search(patched)
if not peak_match_after_fetch:
    raise SystemExit("PATCH_FAILED: peak function lost after fetch replacement.")

patched = (
    patched[:peak_match_after_fetch.start()]
    + peak_replacement
    + patched[peak_match_after_fetch.end():]
)

patched = patched.replace(
    old_call,
    "peak_pnl_pct = qfos_exit_lifecycle_get_peak("
    "conn, symbol, pnl_pct, entry_started_at=p.get('entry_started_at')"
    ")",
    1,
)

try:
    ast.parse(patched)
except SyntaxError as exc:
    raise SystemExit(f"PATCH_FAILED: patched main.py would not parse: {exc}")

path.write_bytes(patched.encode("utf-8"))

print("EXIT_REENTRY_EPOCH_FIX_PATCH_OK")