from pathlib import Path
import ast

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")

marker = "# QFOS_ACTIVE_EXIT_EPOCH_FIX_V2"

if marker in src:
    print("ACTIVE_EXIT_EPOCH_FIX_V2_ALREADY_PRESENT")
    raise SystemExit(0)

tree = ast.parse(src)
lines = src.splitlines(keepends=True)

def one_function(name):
    found = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(found) != 1:
        raise SystemExit(
            f"PATCH_FAILED: expected one {name}, found {len(found)}."
        )
    return found[0]

loader = one_function("_qfos_exit_open_positions_from_db")
decision = one_function("_qfos_exit_decision_for_position")
atomic = one_function("qfos_persist_fill_atomic")

decision_insert_at = decision.end_lineno

epoch_wrapper = r'''

# QFOS_ACTIVE_EXIT_EPOCH_FIX_V2
# Preserve original lifecycle functions, then replace only their state source.
_qfos_exit_open_positions_from_db_original = _qfos_exit_open_positions_from_db
_qfos_exit_decision_for_position_original = _qfos_exit_decision_for_position

def _qfos_exit_open_positions_from_db():
    """
    Active lifecycle loader with fresh-entry age truth.

    The prior loader used MIN(BUY) across all symbol history, causing a new
    re-entry to inherit age from a closed historical trade.
    """
    rows = _qfos_exit_open_positions_from_db_original()

    if not rows:
        return rows

    try:
        with engine.begin() as conn:
            for pos in rows:
                symbol = str(pos.get("symbol") or "").strip()
                if not symbol:
                    continue

                row = conn.execute(text("""
                    SELECT
                        MAX(created_at) AS latest_buy_at
                    FROM trades
                    WHERE symbol = :symbol
                      AND LOWER(side) = 'buy'
                """), {"symbol": symbol}).mappings().first()

                latest_buy_at = (row or {}).get("latest_buy_at")
                if latest_buy_at is None:
                    continue

                age_row = conn.execute(text("""
                    SELECT EXTRACT(
                        EPOCH FROM (
                            CURRENT_TIMESTAMP - :latest_buy_at
                        )
                    ) / 60.0 AS age_minutes
                """), {
                    "latest_buy_at": latest_buy_at,
                }).mappings().first()

                age_minutes = float(
                    ((age_row or {}).get("age_minutes")) or 0.0
                )

                pos["entry_started_at"] = latest_buy_at
                pos["age_minutes"] = max(0.0, age_minutes)

    except Exception as exc:
        print(
            f"[EXIT_ACTIVE_EPOCH_LOADER_ERROR] error={exc!r}",
            flush=True,
        )

    return rows


def _qfos_exit_decision_for_position(pos, regime):
    """
    Reset peak state whenever the active loader identifies a fresh epoch.
    """
    symbol = str((pos or {}).get("symbol") or "").strip()
    epoch = str((pos or {}).get("entry_started_at") or "")

    try:
        epoch_map = globals().setdefault("_qfos_exit_peak_epochs", {})
        prior_epoch = epoch_map.get(symbol)

        if symbol and epoch and prior_epoch != epoch:
            _qfos_exit_peak_pct.pop(symbol, None)
            epoch_map[symbol] = epoch

            print(
                f"[EXIT_ACTIVE_EPOCH_RESET] "
                f"symbol={symbol} "
                f"entry_started_at={epoch}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[EXIT_ACTIVE_EPOCH_RESET_ERROR] "
            f"symbol={symbol} error={exc!r}",
            flush=True,
        )

    return _qfos_exit_decision_for_position_original(pos, regime)
'''

lines.insert(decision_insert_at, epoch_wrapper)
patched = "".join(lines)

tree2 = ast.parse(patched)
atomic2 = [
    node for node in ast.walk(tree2)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "qfos_persist_fill_atomic"
]

if len(atomic2) != 1:
    raise SystemExit(
        "PATCH_FAILED: atomic persistence function changed unexpectedly."
    )

atomic_line = atomic2[0].lineno
patched_lines = patched.splitlines(keepends=True)

firewall = r'''
    # QFOS_ACTIVE_EXIT_EPOCH_FIX_V2
    # Final SELL truth firewall. Prevent a stale DB lifecycle snapshot from
    # persisting an early stagnation or false take-profit exit.
    try:
        _qfos_exit_fill = fill if isinstance(fill, dict) else {}
        _qfos_exit_side = str(_qfos_exit_fill.get("side") or "").lower().strip()
        _qfos_exit_reason = str(
            _qfos_exit_fill.get("exit_reason")
            or _qfos_exit_fill.get("reason")
            or _qfos_exit_fill.get("strategy")
            or ""
        ).lower().strip()

        if _qfos_exit_side == "sell" and _qfos_exit_reason in {
            "sideways_stagnation_exit",
            "sideways_take_profit_exit",
        }:
            _qfos_exit_symbol = str(_qfos_exit_fill.get("symbol") or "").strip()
            _qfos_exit_price = float(
                _qfos_exit_fill.get("fill_price")
                or _qfos_exit_fill.get("expected_price")
                or _qfos_exit_fill.get("price")
                or 0.0
            )

            if _qfos_exit_symbol and _qfos_sa_text is not None:
                _qfos_latest_buy = conn.execute(_qfos_sa_text("""
                    SELECT
                        fill_price,
                        created_at
                    FROM trades
                    WHERE symbol = :symbol
                      AND LOWER(side) = 'buy'
                    ORDER BY id DESC
                    LIMIT 1
                """), {
                    "symbol": _qfos_exit_symbol,
                }).mappings().first()

                if _qfos_latest_buy:
                    _qfos_buy_price = float(
                        _qfos_latest_buy.get("fill_price") or 0.0
                    )

                    _qfos_age_row = conn.execute(_qfos_sa_text("""
                        SELECT EXTRACT(
                            EPOCH FROM (
                                CURRENT_TIMESTAMP - :created_at
                            )
                        ) / 60.0 AS age_minutes
                    """), {
                        "created_at": _qfos_latest_buy.get("created_at"),
                    }).mappings().first()

                    _qfos_age_minutes = float(
                        ((_qfos_age_row or {}).get("age_minutes")) or 0.0
                    )

                    if (
                        _qfos_exit_reason == "sideways_stagnation_exit"
                        and _qfos_age_minutes < float(
                            globals().get(
                                "QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE",
                                20.0,
                            )
                        )
                    ):
                        print(
                            "[EXIT_SELL_BLOCK] "
                            f"symbol={_qfos_exit_symbol} "
                            f"reason=sideways_stagnation_before_min_age "
                            f"age_min={_qfos_age_minutes:.4f}",
                            flush=True,
                        )
                        return False

                    _qfos_tp = float(
                        globals().get(
                            "QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT",
                            globals().get(
                                "QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT",
                                0.0055,
                            ),
                        )
                    )

                    if (
                        _qfos_exit_reason == "sideways_take_profit_exit"
                        and _qfos_buy_price > 0
                        and _qfos_exit_price < (
                            _qfos_buy_price * (1.0 + _qfos_tp)
                        )
                    ):
                        print(
                            "[EXIT_SELL_BLOCK] "
                            f"symbol={_qfos_exit_symbol} "
                            f"reason=false_sideways_take_profit "
                            f"buy_price={_qfos_buy_price:.12f} "
                            f"sell_price={_qfos_exit_price:.12f} "
                            f"required_tp={_qfos_tp:.6f}",
                            flush=True,
                        )
                        return False

    except Exception as _qfos_exit_firewall_error:
        print(
            f"[EXIT_SELL_BLOCK_ERROR] "
            f"error={_qfos_exit_firewall_error!r}",
            flush=True,
        )
        # Fail closed for only the two unreliable lifecycle exit labels.
        try:
            if _qfos_exit_reason in {
                "sideways_stagnation_exit",
                "sideways_take_profit_exit",
            }:
                return False
        except Exception:
            pass

'''

# Insert immediately after the atomic function signature.
patched_lines.insert(atomic_line, firewall)
patched = "".join(patched_lines)

try:
    ast.parse(patched)
except SyntaxError as exc:
    raise SystemExit(f"PATCH_FAILED: patched main.py would not parse: {exc}")

path.write_bytes(patched.encode("utf-8"))

print(
    "ACTIVE_EXIT_EPOCH_FIX_V2_PATCH_OK "
    f"loader_line={loader.lineno} "
    f"decision_line={decision.lineno} "
    f"atomic_line={atomic.lineno}"
)