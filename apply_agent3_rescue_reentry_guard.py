from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_AGENT3_RESCUE_REENTRY_GUARD_V1"
anchor = "# QFOS_EXPECTANCY_EARLY_HOOK_START"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

if anchor not in src:
    raise SystemExit(f"PATCH_FAILED: anchor not found: {anchor}")

patch = r'''
# QFOS_AGENT3_RESCUE_REENTRY_GUARD_V1
# Final rescue-only entry gate. Does not alter exits, accounting, feature generation,
# execution bridge, exposure limits, or API behavior.

_qfos_agent3_rescue_base_expectancy_guard = qfos_expectancy_cycle_guard

QFOS_AGENT3_RESCUE_POST_STOP_COOLDOWN_MINUTES = 30
QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT = 3
QFOS_AGENT3_RESCUE_LOSS_STREAK_BLOCK_MINUTES = 120
QFOS_AGENT3_RESCUE_DUST_QTY = 1e-8

def _qfos_agent3_rescue_float(value, default=0.0):
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default

def _qfos_agent3_rescue_feature_map(context):
    if not isinstance(context, dict):
        return {}
    for key in ("features", "feature_map", "market_features"):
        value = context.get(key)
        if isinstance(value, dict):
            return value
    return {}

def _qfos_agent3_rescue_regime(context):
    if not isinstance(context, dict):
        return ""
    regime = str(context.get("regime") or context.get("market_regime") or "").upper()
    if regime:
        return regime
    portfolio_ctx = context.get("portfolio")
    if isinstance(portfolio_ctx, dict):
        return str(portfolio_ctx.get("regime") or "").upper()
    return ""

def _qfos_agent3_rescue_positions_from_context(context):
    out = {}
    if not isinstance(context, dict):
        return out
    raw = (
        context.get("positions")
        or context.get("open_positions")
        or context.get("portfolio_positions")
        or {}
    )
    if isinstance(raw, dict):
        for symbol, pos in raw.items():
            if isinstance(pos, dict):
                out[str(pos.get("symbol") or symbol)] = _qfos_agent3_rescue_float(
                    pos.get("quantity") or pos.get("qty")
                )
            else:
                out[str(symbol)] = _qfos_agent3_rescue_float(
                    getattr(pos, "quantity", getattr(pos, "qty", 0.0))
                )
    elif isinstance(raw, list):
        for pos in raw:
            if isinstance(pos, dict):
                symbol = str(pos.get("symbol") or "")
                qty = _qfos_agent3_rescue_float(pos.get("quantity") or pos.get("qty"))
            else:
                symbol = str(getattr(pos, "symbol", "") or "")
                qty = _qfos_agent3_rescue_float(
                    getattr(pos, "quantity", getattr(pos, "qty", 0.0))
                )
            if symbol:
                out[symbol] = qty
    return out

def _qfos_agent3_rescue_db_state(symbol):
    """
    Returns quarantine and open-position facts from Postgres.
    Any DB failure is treated as fail-closed for rescue buys.
    """
    state = {
        "db_ok": False,
        "quarantined": False,
        "blocked_until": None,
        "quarantine_reason": "",
        "open_qty": 0.0,
        "recent_stop_losses": 0,
        "db_error": "",
    }
    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            qrow = conn.execute(text("""
                SELECT reason, blocked_until
                FROM symbol_quarantine
                WHERE symbol = :symbol
                  AND blocked_until IS NOT NULL
                  AND blocked_until > CURRENT_TIMESTAMP
                ORDER BY blocked_until DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            prow = conn.execute(text("""
                SELECT COALESCE(SUM(quantity), 0) AS open_qty
                FROM positions
                WHERE symbol = :symbol
                  AND quantity > :dust
            """), {
                "symbol": symbol,
                "dust": QFOS_AGENT3_RESCUE_DUST_QTY,
            }).mappings().first()

            lrow = conn.execute(text("""
                SELECT COUNT(*) AS stop_count
                FROM trades
                WHERE symbol = :symbol
                  AND LOWER(side) = 'sell'
                  AND COALESCE(exit_reason, strategy, '') = 'sideways_stop_loss_exit'
                  AND created_at >= CURRENT_TIMESTAMP - interval '2 hours'
            """), {"symbol": symbol}).mappings().first()

        state["db_ok"] = True
        state["open_qty"] = _qfos_agent3_rescue_float(
            (prow or {}).get("open_qty")
        )
        state["recent_stop_losses"] = int(
            _qfos_agent3_rescue_float((lrow or {}).get("stop_count"))
        )

        if qrow:
            state["quarantined"] = True
            state["quarantine_reason"] = str(qrow.get("reason") or "")
            state["blocked_until"] = str(qrow.get("blocked_until") or "")

    except Exception as exc:
        state["db_error"] = repr(exc)

    return state

def _qfos_agent3_rescue_record_stop_loss(symbol):
    """
    Creates 30-minute post-stop-loss cooldown. On third recent stop, upgrades
    to a 2-hour loss-streak block. This only writes symbol_quarantine metadata.
    """
    if not symbol:
        return

    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            prior_row = conn.execute(text("""
                SELECT COUNT(*) AS stop_count
                FROM trades
                WHERE symbol = :symbol
                  AND LOWER(side) = 'sell'
                  AND COALESCE(exit_reason, strategy, '') = 'sideways_stop_loss_exit'
                  AND created_at >= CURRENT_TIMESTAMP - interval '2 hours'
            """), {"symbol": symbol}).mappings().first()

            prior_count = int(
                _qfos_agent3_rescue_float((prior_row or {}).get("stop_count"))
            )

            projected_count = prior_count + 1
            if projected_count >= QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT:
                minutes = QFOS_AGENT3_RESCUE_LOSS_STREAK_BLOCK_MINUTES
                reason = "sideways_stop_loss_loss_streak"
            else:
                minutes = QFOS_AGENT3_RESCUE_POST_STOP_COOLDOWN_MINUTES
                reason = "sideways_stop_loss_cooldown"

            conn.execute(text("""
                INSERT INTO symbol_quarantine(
                    symbol,
                    reason,
                    blocked_until,
                    created_at
                )
                VALUES (
                    :symbol,
                    :reason,
                    CURRENT_TIMESTAMP + (:minutes * interval '1 minute'),
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (symbol) DO UPDATE SET
                    reason = EXCLUDED.reason,
                    blocked_until = GREATEST(
                        COALESCE(symbol_quarantine.blocked_until, CURRENT_TIMESTAMP),
                        EXCLUDED.blocked_until
                    ),
                    created_at = EXCLUDED.created_at
            """), {
                "symbol": symbol,
                "reason": reason,
                "minutes": minutes,
            })

        print(
            f"[RESCUE_STOP_LOSS_QUARANTINE] symbol={symbol} "
            f"reason={reason} stop_loss_count={projected_count} "
            f"cooldown_minutes={minutes}",
            flush=True,
        )

    except Exception as exc:
        print(
            f"[RESCUE_STOP_LOSS_QUARANTINE_ERROR] symbol={symbol} "
            f"error={exc!r}",
            flush=True,
        )

def _qfos_agent3_rescue_rank_map(feature_map):
    """
    Evidence-only local ranking for rescue gating.
    It deliberately gives no credit to non-positive one-tick momentum.
    """
    rows = []

    for symbol, feature in (feature_map or {}).items():
        if not isinstance(feature, dict):
            continue

        source = str(feature.get("source") or "").upper()
        ready = bool(feature.get("ready"))
        symbol_regime = str(feature.get("symbol_regime") or "").upper()

        if source != "NORMAL" or not ready:
            continue

        if symbol_regime not in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):
            continue

        signal = _qfos_agent3_rescue_float(
            feature.get("signal_strength", feature.get("signal", 0.0))
        )
        breakout = _qfos_agent3_rescue_float(feature.get("breakout_score"))
        trend_quality = _qfos_agent3_rescue_float(
            feature.get("trend_quality", feature.get("symbol_trend_score", 0.0))
        )
        momentum = _qfos_agent3_rescue_float(feature.get("momentum"))
        one_tick = _qfos_agent3_rescue_float(feature.get("one_tick_momentum"))

        if one_tick <= 0 or breakout <= 0 or trend_quality <= 0:
            continue

        score = (
            signal * 100.0
            + breakout * 100.0
            + trend_quality * 100.0
            + max(momentum, 0.0) * 25.0
            + max(one_tick, 0.0) * 25.0
        )
        rows.append((symbol, score))

    rows.sort(key=lambda item: item[1], reverse=True)
    return {symbol: index + 1 for index, (symbol, _) in enumerate(rows)}

def _qfos_agent3_rescue_emit(
    symbol,
    decision,
    reason,
    confidence,
    signal,
    one_tick,
    breakout,
    trend_quality,
    rank,
    source,
    ready,
    recent_stop_losses,
    quarantined,
    open_symbol_qty,
):
    print(
        "[RESCUE_DECISION] "
        f"symbol={symbol} decision={decision} reason={reason} "
        f"confidence={confidence:.6f} signal={signal:.8f} "
        f"one_tick_momentum={one_tick:.8f} breakout_score={breakout:.8f} "
        f"trend_quality={trend_quality:.8f} rank={rank} "
        f"source={source} ready={ready} "
        f"recent_stop_losses={recent_stop_losses} "
        f"quarantined={quarantined} open_symbol_qty={open_symbol_qty:.12f}",
        flush=True,
    )

def _qfos_agent3_rescue_reentry_guard(proposed_fills, context):
    try:
        orders = list(proposed_fills or [])
    except Exception:
        return []

    feature_map = _qfos_agent3_rescue_feature_map(context)
    regime = _qfos_agent3_rescue_regime(context)
    context_positions = _qfos_agent3_rescue_positions_from_context(context)
    rank_map = _qfos_agent3_rescue_rank_map(feature_map)

    try:
        top_n = max(1, int(ENTRY_QUALITY_TOP_N))
    except Exception:
        top_n = 10

    try:
        min_signal_sideways = float(ENTRY_MIN_SIGNAL_SIDEWAYS)
    except Exception:
        min_signal_sideways = 0.0016

    # Register stop-loss quarantine before later rescue candidates can be evaluated.
    for order in orders:
        if not isinstance(order, dict):
            continue

        side = str(order.get("side") or "").lower()
        reason = str(
            order.get("exit_reason")
            or order.get("strategy")
            or order.get("reason")
            or ""
        )
        symbol = str(order.get("symbol") or "")

        if side == "sell" and reason == "sideways_stop_loss_exit":
            _qfos_agent3_rescue_record_stop_loss(symbol)

    filtered = []

    for order in orders:
        if not isinstance(order, dict):
            filtered.append(order)
            continue

        side = str(order.get("side") or "").lower()
        strategy = str(order.get("strategy") or order.get("reason") or "")
        symbol = str(order.get("symbol") or "")

        if side != "buy" or strategy != "evo_allocator_rescue":
            filtered.append(order)
            continue

        feature = feature_map.get(symbol, {})
        if not isinstance(feature, dict):
            feature = {}

        source = str(feature.get("source") or "").upper()
        ready = bool(feature.get("ready"))
        symbol_regime = str(feature.get("symbol_regime") or "").upper()

        signal = _qfos_agent3_rescue_float(
            order.get("signal_strength")
            or feature.get("signal_strength")
            or feature.get("signal")
        )
        one_tick = _qfos_agent3_rescue_float(feature.get("one_tick_momentum"))
        breakout = _qfos_agent3_rescue_float(feature.get("breakout_score"))
        trend_quality = _qfos_agent3_rescue_float(
            feature.get("trend_quality")
            or feature.get("symbol_trend_score")
        )
        momentum = _qfos_agent3_rescue_float(feature.get("momentum"))

        rank = rank_map.get(symbol, 0)
        db_state = _qfos_agent3_rescue_db_state(symbol)
        context_open_qty = _qfos_agent3_rescue_float(context_positions.get(symbol))
        open_qty = max(context_open_qty, _qfos_agent3_rescue_float(db_state["open_qty"]))
        recent_stop_losses = int(db_state["recent_stop_losses"])
        quarantined = bool(db_state["quarantined"])

        # Evidence-derived confidence. This replaces rescue defaults such as 0.95.
        evidence_confidence = min(
            0.90,
            max(
                0.0,
                0.35
                + min(max(signal, 0.0) * 8.0, 0.20)
                + min(max(one_tick, 0.0) * 80.0, 0.12)
                + min(max(breakout, 0.0) * 8.0, 0.12)
                + min(max(trend_quality, 0.0) * 8.0, 0.11),
            ),
        )
        order["confidence"] = round(evidence_confidence, 6)

        reject_reason = ""

        if not db_state["db_ok"]:
            reject_reason = "rescue_db_check_error"
        elif quarantined:
            if "stop_loss" in str(db_state["quarantine_reason"]):
                reject_reason = "recent_stop_loss"
            else:
                reject_reason = "quarantined"
        elif recent_stop_losses >= QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT:
            reject_reason = "loss_streak"
        elif open_qty > QFOS_AGENT3_RESCUE_DUST_QTY:
            reject_reason = "existing_position_no_scale_in"
        elif source != "NORMAL" or not ready:
            reject_reason = "confidence_not_evidence_backed"
        elif "SIDEWAYS" in regime and symbol_regime not in (
            "SYMBOL_BREAKOUT_UP",
            "SYMBOL_TREND_UP",
        ):
            reject_reason = "symbol_regime_not_allowed"
        elif "SIDEWAYS" in regime and signal < min_signal_sideways:
            reject_reason = "sideways_signal_below_threshold"
        elif "SIDEWAYS" in regime and one_tick <= 0:
            reject_reason = "weak_one_tick_confirmation"
        elif "SIDEWAYS" in regime and breakout <= 0:
            reject_reason = "breakout_quality_below_threshold"
        elif "SIDEWAYS" in regime and trend_quality <= 0:
            reject_reason = "trend_quality_below_threshold"
        elif "SIDEWAYS" in regime and rank <= 0:
            reject_reason = "not_ranked_evidence_candidate"
        elif "SIDEWAYS" in regime and rank > top_n:
            reject_reason = "not_entry_quality_top_n"
        elif evidence_confidence < 0.50:
            reject_reason = "confidence_not_evidence_backed"

        if reject_reason:
            extra = ""
            if reject_reason == "recent_stop_loss":
                extra = f" blocked_until={db_state['blocked_until']}"
            elif reject_reason == "loss_streak":
                extra = f" stop_loss_count={recent_stop_losses}"
            elif reject_reason == "existing_position_no_scale_in":
                extra = f" open_symbol_qty={open_qty:.12f}"

            print(
                f"[RESCUE_REJECT] symbol={symbol} reason={reject_reason}{extra}",
                flush=True,
            )

            _qfos_agent3_rescue_emit(
                symbol=symbol,
                decision="REJECT",
                reason=reject_reason,
                confidence=evidence_confidence,
                signal=signal,
                one_tick=one_tick,
                breakout=breakout,
                trend_quality=trend_quality,
                rank=rank,
                source=source,
                ready=ready,
                recent_stop_losses=recent_stop_losses,
                quarantined=quarantined,
                open_symbol_qty=open_qty,
            )
            continue

        _qfos_agent3_rescue_emit(
            symbol=symbol,
            decision="ALLOW",
            reason="evidence_gates_passed",
            confidence=evidence_confidence,
            signal=signal,
            one_tick=one_tick,
            breakout=breakout,
            trend_quality=trend_quality,
            rank=rank,
            source=source,
            ready=ready,
            recent_stop_losses=recent_stop_losses,
            quarantined=quarantined,
            open_symbol_qty=open_qty,
        )

        filtered.append(order)

    return filtered

def qfos_expectancy_cycle_guard(proposed_fills, context):
    base_orders = _qfos_agent3_rescue_base_expectancy_guard(proposed_fills, context)
    return _qfos_agent3_rescue_reentry_guard(base_orders, context)

'''

src = src.replace(anchor, patch + "\n" + anchor, 1)
path.write_text(src, encoding="utf-8")

print("PATCH_WRITE_OK")
