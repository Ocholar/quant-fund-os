from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_EXIT_LIFECYCLE_DB_PATCH_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

patch = r'''

# QFOS_EXIT_LIFECYCLE_DB_PATCH_V1
# Joint Agent 2 + Agent 5
# Purpose:
#   - Evaluate every DB open position every cycle.
#   - Emit one [EXIT_DECISION] log per open position.
#   - Inject SELL fills into the existing execution/accounting pipeline.
#   - Do not change BUY allocation, feature generation, cash accounting, or live mode.

QFOS_EXIT_LIFECYCLE_ENABLED = globals().get("QFOS_EXIT_LIFECYCLE_ENABLED", True)

QFOS_EXIT_TAKE_PROFIT_PCT = globals().get("QFOS_EXIT_TAKE_PROFIT_PCT", 0.012)          # +1.20%
QFOS_EXIT_STOP_LOSS_PCT = globals().get("QFOS_EXIT_STOP_LOSS_PCT", -0.008)             # -0.80%

QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT = globals().get("QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT", 0.006)  # +0.60%
QFOS_SIDEWAYS_EXIT_STOP_LOSS_PCT = globals().get("QFOS_SIDEWAYS_EXIT_STOP_LOSS_PCT", -0.006)     # -0.60%

QFOS_SIDEWAYS_STAGNATION_MINUTES = globals().get("QFOS_SIDEWAYS_STAGNATION_MINUTES", 20.0)
QFOS_SIDEWAYS_STAGNATION_LOW_PCT = globals().get("QFOS_SIDEWAYS_STAGNATION_LOW_PCT", -0.0025)
QFOS_SIDEWAYS_STAGNATION_HIGH_PCT = globals().get("QFOS_SIDEWAYS_STAGNATION_HIGH_PCT", 0.0035)

QFOS_MAX_HOLD_MINUTES = globals().get("QFOS_MAX_HOLD_MINUTES", 45.0)

QFOS_TRAILING_PEAK_PCT = globals().get("QFOS_TRAILING_PEAK_PCT", 0.0045)
QFOS_TRAILING_FLOOR_PCT = globals().get("QFOS_TRAILING_FLOOR_PCT", 0.0015)

QFOS_BREAKEVEN_PEAK_PCT = globals().get("QFOS_BREAKEVEN_PEAK_PCT", 0.0035)
QFOS_BREAKEVEN_FLOOR_PCT = globals().get("QFOS_BREAKEVEN_FLOOR_PCT", 0.0002)

_qfos_exit_peak_pct = {}


def _qfos_exit_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_exit_side(value):
    return str(value or "").strip().lower()


def _qfos_exit_now_sql_expr():
    # DB stores user-facing local time in many places; use the same +3h convention.
    return "CURRENT_TIMESTAMP + interval '3 hours'"


def _qfos_exit_open_positions_from_db():
    """
    Read DB positions with age and PnL.

    This avoids the old bug where the cycle guard received locals()
    but no normalized positions object, resulting in exits=0.
    """
    rows = []

    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                select
                    p.symbol,
                    p.quantity,
                    p.avg_entry,
                    coalesce(p.last_price, p.avg_entry) as last_price,
                    coalesce(p.exposure, p.quantity * coalesce(p.last_price, p.avg_entry)) as exposure,
                    coalesce(p.unrealized_pnl, (coalesce(p.last_price, p.avg_entry) - p.avg_entry) * p.quantity) as unrealized_pnl,
                    coalesce(p.strategy, 'unknown_strategy') as strategy,
                    coalesce(
                        (
                            select min(t.created_at)
                            from trades t
                            where t.symbol = p.symbol
                              and lower(t.side) = 'buy'
                        ),
                        p.updated_at,
                        CURRENT_TIMESTAMP
                    ) as first_buy_at,
                    extract(epoch from ((CURRENT_TIMESTAMP + interval '3 hours') - coalesce(
                        (
                            select min(t.created_at)
                            from trades t
                            where t.symbol = p.symbol
                              and lower(t.side) = 'buy'
                        ),
                        p.updated_at,
                        CURRENT_TIMESTAMP
                    ))) / 60.0 as age_minutes
                from positions p
                where coalesce(p.quantity, 0) > 0.00000001
                order by age_minutes desc
            """)).mappings().all()
    except Exception as exc:
        print(f"[EXIT_DECISION] db_read_failed error={repr(exc)}", flush=True)
        return []

    return [dict(r) for r in rows]


def _qfos_exit_runner_conditions_true(pos, pnl_pct, peak_pct, regime):
    """
    Strict runner rule.

    Do not use vague optimism to hold dead SIDEWAYS positions.
    Runner protection only applies to clearly green positions.
    """
    try:
        if pnl_pct >= 0.006 and peak_pct >= 0.006:
            return True
        if str(regime or "").upper() != "SIDEWAYS" and pnl_pct >= 0.004 and peak_pct >= 0.004:
            return True
    except Exception:
        pass
    return False


def _qfos_exit_decision_for_position(pos, regime):
    symbol = str(pos.get("symbol") or "").strip()
    qty = _qfos_exit_float(pos.get("quantity"))
    avg_entry = _qfos_exit_float(pos.get("avg_entry"))
    last_price = _qfos_exit_float(pos.get("last_price"), avg_entry)
    age_minutes = _qfos_exit_float(pos.get("age_minutes"))

    if not symbol:
        return None

    if qty <= 0:
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_no_open_quantity",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "age_minutes": age_minutes,
        }

    if avg_entry <= 0 or last_price <= 0:
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_invalid_price",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "age_minutes": age_minutes,
        }

    pnl_pct = (last_price - avg_entry) / avg_entry
    peak_pnl_pct = max(_qfos_exit_peak_pct.get(symbol, pnl_pct), pnl_pct)
    _qfos_exit_peak_pct[symbol] = peak_pnl_pct

    is_sideways = str(regime or "").upper() == "SIDEWAYS"
    runner = _qfos_exit_runner_conditions_true(pos, pnl_pct, peak_pnl_pct, regime)

    tp = QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT if is_sideways else QFOS_EXIT_TAKE_PROFIT_PCT
    sl = QFOS_SIDEWAYS_EXIT_STOP_LOSS_PCT if is_sideways else QFOS_EXIT_STOP_LOSS_PCT

    # 1. Take profit
    if pnl_pct >= tp:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "sideways_take_profit_exit" if is_sideways else "take_profit_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 2. Stop loss
    if pnl_pct <= sl:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "sideways_stop_loss_exit" if is_sideways else "stop_loss_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 5. Trailing profit protection
    if peak_pnl_pct >= QFOS_TRAILING_PEAK_PCT and pnl_pct <= QFOS_TRAILING_FLOOR_PCT:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "trailing_profit_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 6. Breakeven protection
    if peak_pnl_pct >= QFOS_BREAKEVEN_PEAK_PCT and pnl_pct <= QFOS_BREAKEVEN_FLOOR_PCT:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "breakeven_protection_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 3. Sideways stagnation exit
    if is_sideways and age_minutes >= QFOS_SIDEWAYS_STAGNATION_MINUTES:
        if QFOS_SIDEWAYS_STAGNATION_LOW_PCT <= pnl_pct <= QFOS_SIDEWAYS_STAGNATION_HIGH_PCT:
            if not runner:
                return {
                    "symbol": symbol,
                    "decision": "SELL",
                    "reason": "sideways_stagnation_exit",
                    "quantity": qty,
                    "price": last_price,
                    "pnl_pct": pnl_pct,
                    "peak_pnl_pct": peak_pnl_pct,
                    "age_minutes": age_minutes,
                }

            return {
                "symbol": symbol,
                "decision": "HOLD",
                "reason": "hold_runner_conditions_true",
                "quantity": qty,
                "price": last_price,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "age_minutes": age_minutes,
            }

    # 4. Max hold exit
    if age_minutes >= QFOS_MAX_HOLD_MINUTES:
        if not runner:
            return {
                "symbol": symbol,
                "decision": "SELL",
                "reason": "sideways_max_hold_exit" if is_sideways else "max_hold_exit",
                "quantity": qty,
                "price": last_price,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "age_minutes": age_minutes,
            }

        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_runner_conditions_true",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    hold_reasons = []

    if age_minutes < QFOS_SIDEWAYS_STAGNATION_MINUTES:
        hold_reasons.append("hold_not_old_enough")

    if pnl_pct < tp:
        hold_reasons.append("hold_take_profit_not_hit")

    if pnl_pct > sl:
        hold_reasons.append("hold_stop_loss_not_hit")

    if not hold_reasons:
        hold_reasons.append("hold_exit_threshold_not_met")

    return {
        "symbol": symbol,
        "decision": "HOLD",
        "reason": "|".join(hold_reasons),
        "quantity": qty,
        "price": last_price,
        "pnl_pct": pnl_pct,
        "peak_pnl_pct": peak_pnl_pct,
        "age_minutes": age_minutes,
    }


def _qfos_exit_log_decision(d):
    try:
        print(
            "[EXIT_DECISION] "
            f"symbol={d.get('symbol')} "
            f"age_min={_qfos_exit_float(d.get('age_minutes')):.2f} "
            f"pnl_pct={_qfos_exit_float(d.get('pnl_pct')):.4%} "
            f"peak_pnl_pct={_qfos_exit_float(d.get('peak_pnl_pct')):.4%} "
            f"decision={d.get('decision')} "
            f"reason={d.get('reason')}",
            flush=True,
        )
    except Exception as exc:
        print(f"[EXIT_DECISION] log_failed error={repr(exc)}", flush=True)


def _qfos_exit_build_sell_fill(d):
    symbol = str(d.get("symbol") or "").strip()
    qty = _qfos_exit_float(d.get("quantity"))
    price = _qfos_exit_float(d.get("price"))
    reason = str(d.get("reason") or "").strip()

    if not symbol or qty <= 0 or price <= 0 or not reason:
        return None

    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "qty": qty,
        "price": price,
        "fill_price": price,
        "expected_price": price,
        "strategy": reason,
        "reason": reason,
        "exit_reason": reason,
        "is_exit": True,
        "confidence": 1.0,
        "source": "exit_lifecycle",
    }


def qfos_exit_lifecycle_db_sells(regime):
    """
    Return SELL fills for qualifying DB open positions.
    Logs one EXIT_DECISION per position every cycle.
    """
    if not QFOS_EXIT_LIFECYCLE_ENABLED:
        return []

    sells = []

    for pos in _qfos_exit_open_positions_from_db():
        decision = _qfos_exit_decision_for_position(pos, regime)
        if not decision:
            continue

        _qfos_exit_log_decision(decision)

        if decision.get("decision") == "SELL":
            fill = _qfos_exit_build_sell_fill(decision)
            if fill:
                sells.append(fill)

    if sells:
        print(
            "[EXIT_LIFECYCLE] injected_sells="
            + str([(s.get("symbol"), s.get("quantity"), s.get("exit_reason")) for s in sells]),
            flush=True,
        )

    return sells


def _qfos_exit_lifecycle_wrap_expectancy_guard():
    """
    Wrap the already-active qfos_expectancy_guard_with_cycle_log.

    Important:
    - Existing expectancy guard remains active.
    - We append DB-backed exit lifecycle SELLs afterward.
    - No BUY logic is changed.
    """
    global qfos_expectancy_guard_with_cycle_log

    old_guard = globals().get("qfos_expectancy_guard_with_cycle_log")

    if not callable(old_guard):
        print("[EXIT_LIFECYCLE] expectancy_guard_not_found; db exits will rely on generate_sells path only", flush=True)
        return

    if getattr(old_guard, "_qfos_exit_lifecycle_wrapped", False):
        return

    def _wrapped_qfos_expectancy_guard_with_exit_lifecycle(proposed_fills=None, context=None):
        try:
            out = old_guard(proposed_fills, context)
        except Exception as exc:
            print("[EXIT_LIFECYCLE] original_expectancy_guard_failed " + repr(exc), flush=True)
            out = list(proposed_fills or [])

        try:
            ctx = context if isinstance(context, dict) else {}
            regime = ctx.get("regime") or ctx.get("market_regime") or globals().get("last_known_regime") or "SIDEWAYS"
            exit_sells = qfos_exit_lifecycle_db_sells(regime)

            if exit_sells:
                existing = list(out or [])
                existing_keys = set()

                for f in existing:
                    if isinstance(f, dict) and _qfos_exit_side(f.get("side")) == "sell":
                        existing_keys.add(str(f.get("symbol") or "").strip())

                clean_exit_sells = [
                    s for s in exit_sells
                    if str(s.get("symbol") or "").strip() not in existing_keys
                ]

                if clean_exit_sells:
                    out = clean_exit_sells + existing

        except Exception as exc:
            print("[EXIT_LIFECYCLE] db_exit_injection_failed " + repr(exc), flush=True)

        return out

    _wrapped_qfos_expectancy_guard_with_exit_lifecycle._qfos_exit_lifecycle_wrapped = True
    qfos_expectancy_guard_with_cycle_log = _wrapped_qfos_expectancy_guard_with_exit_lifecycle
    print("[EXIT_LIFECYCLE] wrapped qfos_expectancy_guard_with_cycle_log", flush=True)


_qfos_exit_lifecycle_wrap_expectancy_guard()

# END QFOS_EXIT_LIFECYCLE_DB_PATCH_V1
'''

# Best placement: after the existing expectancy wrapper is defined, before main loop starts.
anchors = [
    "# QFOS_EXPECTANCY_EARLY_HOOK_END",
    "# QFOS_WINNING_STRATEGY_PATCH_START",
    "def allow_risk_off_exit",
]

insert_at = None
for anchor in anchors:
    idx = text.find(anchor)
    if idx != -1:
        insert_at = idx
        break

if insert_at is None:
    # Fallback: place after qfos_expectancy_guard_with_cycle_log name appears.
    idx = text.find("qfos_expectancy_guard_with_cycle_log")
    if idx == -1:
        raise SystemExit("PATCH_FAILED: could not find expectancy guard anchor")
    line_end = text.find("\n", idx)
    insert_at = line_end + 1

text = text[:insert_at] + patch + "\n\n" + text[insert_at:]

path.write_text(text, encoding="utf-8")

print("PATCH_WRITE_OK")
