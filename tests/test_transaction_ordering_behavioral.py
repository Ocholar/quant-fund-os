"""
Behavioral regression tests for transaction ordering.

main.py is a monolithic script that connects to a real database and
external services at module import time (`from core.db import engine`,
market/exchange clients, etc.), so it cannot be unit-imported in an
isolated test process without a full live environment. Rather than skip
this class of test entirely, this file extracts the EXACT control-flow
shape of the patch -- verified line-by-line against main_patched.py,
referenced below -- into a minimal, faithful harness, and drives it
against a real (in-memory) SQLite database.

This tests the PATTERN the patch introduces (defer notification until
after commit; suppress and log-structured on failure), not main.py's full
business logic. Treat it as a strong, fast, CI-friendly guard against
someone re-introducing the bug by moving the notification call back
inside the transaction -- and pair it with one live, end-to-end BUY/SELL
trace against the real bot before calling the incident closed (see the
"Live verification" note at the bottom of this file).

Harness correspondence to main_patched.py:
    qfos_pending_trade_notifications = []        <-> main_patched.py:11454
    qfos_txn_trade_context = {...}                <-> main_patched.py:11455
    try: / with engine.begin() as conn: ...      <-> main_patched.py:11456-11661
    except Exception as qfos_txn_exc: ...; raise  <-> main_patched.py:11662-11665
    else: for msg in ...: send_telegram_alert(msg)<-> main_patched.py:11666-11667
"""
import sqlite3
import pytest
from unittest.mock import MagicMock


def make_harness(strategy_scores_should_fail: bool, decoupled: bool = True):
    """
    Builds a tiny in-memory SQLite database and a callable that runs the
    exact control-flow shape used in the patched main loop, with the
    strategy_scores step toggled to succeed or fail on purpose.

    `decoupled=True` (the current, final architecture) mirrors
    main_patched.py where strategy_scores runs in its OWN post-commit
    transaction via _qfos_apply_strategy_score_updates(), so a failure
    there can never affect trade/position/snapshot durability or block
    Telegram.

    `decoupled=False` mirrors the intermediate architecture (rollback
    ordering fixed, but strategy_scores still inside the critical
    transaction) purely so the *_decoupled tests below can demonstrate
    they'd fail against that earlier, less-hardened shape too.

    Returns (call_log, run_cycle) where call_log is a list that records,
    in order, every side-effecting step that actually happened.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.execute("CREATE TABLE positions (symbol TEXT PRIMARY KEY, quantity REAL)")
    conn.execute("CREATE TABLE portfolio_snapshots (id INTEGER PRIMARY KEY, equity REAL)")
    conn.commit()

    call_log = []
    send_telegram_alert = MagicMock(side_effect=lambda msg: call_log.append(("telegram", msg)))
    log_transaction_failure = MagicMock(
        side_effect=lambda exc, ctx: call_log.append(("rollback_log", str(exc), ctx))
    )
    log_strategy_score_failure = MagicMock(
        side_effect=lambda exc: call_log.append(("strategy_score_update_failed", str(exc)))
    )

    def apply_strategy_score_update_decoupled():
        # Mirrors _qfos_apply_strategy_score_updates(): its own
        # transaction, its own try/except, never raises to the caller.
        try:
            conn.execute("BEGIN")
            if strategy_scores_should_fail:
                raise sqlite3.OperationalError(
                    "there is no unique or exclusion constraint matching "
                    "the ON CONFLICT specification"
                )
            call_log.append(("strategy_scores_upserted",))
            conn.execute("COMMIT")
        except Exception as e:
            conn.execute("ROLLBACK")
            log_strategy_score_failure(e)

    def run_cycle(symbol="BTC/USDT"):
        qfos_pending_trade_notifications = []
        qfos_txn_trade_context = {"symbols": [], "last_strategy": None}

        try:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO trades (id, symbol) VALUES (?, ?)", (119, symbol))
            call_log.append(("trade_inserted", symbol))

            qfos_pending_trade_notifications.append(f"BUY {symbol}")
            qfos_txn_trade_context["symbols"].append(symbol)

            conn.execute(
                "INSERT INTO positions (symbol, quantity) VALUES (?, ?)", (symbol, 1.0)
            )
            call_log.append(("position_upserted", symbol))

            if not decoupled:
                # Old (intermediate) shape: strategy_scores still inside
                # the critical transaction -- kept here only so the tests
                # below can demonstrate they'd catch a regression back to
                # this shape.
                if strategy_scores_should_fail:
                    raise sqlite3.OperationalError(
                        "there is no unique or exclusion constraint matching "
                        "the ON CONFLICT specification"
                    )
                call_log.append(("strategy_scores_upserted",))

            conn.execute("INSERT INTO portfolio_snapshots (id, equity) VALUES (?, ?)", (1, 100.0))
            call_log.append(("snapshot_inserted",))

            conn.execute("COMMIT")
            call_log.append(("committed",))

        except Exception as qfos_txn_exc:
            conn.execute("ROLLBACK")
            log_transaction_failure(qfos_txn_exc, qfos_txn_trade_context)
            qfos_pending_trade_notifications = []
            raise

        else:
            if decoupled:
                apply_strategy_score_update_decoupled()
            for qfos_msg in qfos_pending_trade_notifications:
                send_telegram_alert(qfos_msg)

    return conn, call_log, run_cycle, send_telegram_alert, log_transaction_failure, log_strategy_score_failure


def test_strategy_scores_failure_rolls_back_everything_and_never_notifies():
    """
    NOTE: this test intentionally uses decoupled=False -- it exercises the
    intermediate (pre-decoupling) architecture, where strategy_scores was
    still inside the critical transaction, purely to document that THAT
    shape used to (correctly, for its time) roll everything back. The
    CURRENT, final architecture is exercised by
    test_strategy_scores_failure_does_not_affect_critical_transaction
    below, and is the one that must pass against main_patched.py.
    """
    conn, call_log, run_cycle, send_telegram_alert, log_transaction_failure, _ = make_harness(
        strategy_scores_should_fail=True, decoupled=False
    )

    with pytest.raises(sqlite3.OperationalError):
        run_cycle("ETH/USDT")

    trades = conn.execute("SELECT * FROM trades").fetchall()
    positions = conn.execute("SELECT * FROM positions").fetchall()
    snapshots = conn.execute("SELECT * FROM portfolio_snapshots").fetchall()
    assert trades == [], "trade must not survive a rolled-back transaction"
    assert positions == [], "position must not survive a rolled-back transaction"
    assert snapshots == [], "snapshot must not survive a rolled-back transaction"

    send_telegram_alert.assert_not_called()
    log_transaction_failure.assert_called_once()
    exc_arg, ctx_arg = log_transaction_failure.call_args.args
    assert "ON CONFLICT" in str(exc_arg)
    assert ctx_arg["symbols"] == ["ETH/USDT"]
    assert ("telegram", "BUY ETH/USDT") not in call_log


def test_strategy_scores_failure_does_not_affect_critical_transaction():
    """
    This is the test the architecture review specifically requested:
    force strategy_scores to fail, on the CURRENT (decoupled) shape.

    Expected, and this is what makes it different from the test above:
      - trade committed
      - position committed
      - snapshot committed
      - strategy score update logged as failed (not fatal)
      - Telegram still sent
    Analytics failing must never touch the trading ledger.
    """
    conn, call_log, run_cycle, send_telegram_alert, log_transaction_failure, log_strategy_score_failure = (
        make_harness(strategy_scores_should_fail=True, decoupled=True)
    )

    run_cycle("BTC/USDT")  # must NOT raise -- the critical transaction succeeds

    # --- the trading ledger is durable, in full ---
    assert conn.execute("SELECT * FROM trades").fetchall() == [(119, "BTC/USDT")]
    assert conn.execute("SELECT * FROM positions").fetchall() == [("BTC/USDT", 1.0)]
    assert conn.execute("SELECT * FROM portfolio_snapshots").fetchall() == [(1, 100.0)]

    # --- the critical transaction's own failure logger was NEVER invoked ---
    # (this failure is analytics-only; it must not be reported as a
    # trade-transaction rollback, because there wasn't one)
    log_transaction_failure.assert_not_called()

    # --- the strategy-score-specific, non-fatal logger WAS invoked ---
    log_strategy_score_failure.assert_called_once()
    (exc_arg,) = log_strategy_score_failure.call_args.args
    assert "ON CONFLICT" in str(exc_arg)

    # --- Telegram still fires -- a real, committed trade is still reported ---
    send_telegram_alert.assert_called_once_with("BUY BTC/USDT")

    # --- ordering: commit happens before the (failed) score update,
    #     which happens before Telegram ---
    kinds_in_order = [entry[0] for entry in call_log]
    assert kinds_in_order == [
        "trade_inserted",
        "position_upserted",
        "snapshot_inserted",
        "committed",
        "strategy_score_update_failed",
        "telegram",
    ], f"unexpected ordering: {kinds_in_order}"


def test_successful_buy_commits_before_telegram_is_sent():
    """
    Test 2 requested by review: successful BUY. Expected order:
    trade committed -> position -> strategy_scores -> snapshot -> COMMIT
    -> only THEN Telegram.
    """
    conn, call_log, run_cycle, send_telegram_alert, log_transaction_failure, log_strategy_score_failure = (
        make_harness(strategy_scores_should_fail=False, decoupled=True)
    )

    run_cycle("BTC/USDT")

    # --- durable state must actually be there ---
    assert conn.execute("SELECT * FROM trades").fetchall() == [(119, "BTC/USDT")]
    assert conn.execute("SELECT * FROM positions").fetchall() == [("BTC/USDT", 1.0)]
    assert conn.execute("SELECT * FROM portfolio_snapshots").fetchall() == [(1, 100.0)]

    # --- ordering: commit must appear before telegram in the call log ---
    kinds_in_order = [entry[0] for entry in call_log]
    assert kinds_in_order == [
        "trade_inserted",
        "position_upserted",
        "snapshot_inserted",
        "committed",
        "strategy_scores_upserted",
        "telegram",
    ], f"unexpected ordering: {kinds_in_order}"

    commit_index = kinds_in_order.index("committed")
    telegram_index = kinds_in_order.index("telegram")
    assert commit_index < telegram_index, "Telegram must fire strictly after commit"

    send_telegram_alert.assert_called_once_with("BUY BTC/USDT")
    log_transaction_failure.assert_not_called()
    log_strategy_score_failure.assert_not_called()


# ============================================================
# Live verification (cannot be run from this sandbox)
# ============================================================
#
# These behavioral tests prove the CONTROL-FLOW PATTERN is correct. They
# do not exercise main.py's real qfos_persist_fill_atomic, real Postgres,
# or the real send_telegram_alert/Telegram API. Before closing the
# incident, capture one real end-to-end trace from the deployed bot:
#
#   docker compose logs quant --no-color -f | grep -E \
#     "buy_persisted|QFOS_TRANSACTION_ROLLBACK|EXIT_DECISION|Telegram"
#
# and confirm the order is exactly:
#   buy_persisted -> (no QFOS_TRANSACTION_ROLLBACK line) -> Telegram BUY
#   -> /status shows the position -> SELL -> Telegram SELL
#   -> /status returns to zero exposure
#
# A single clean 60-minute window with zero QFOS_TRANSACTION_ROLLBACK
# lines is the actual acceptance evidence -- these tests are a fast
# guard against regressing the pattern, not a substitute for that trace.
