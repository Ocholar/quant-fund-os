from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_AGENT3_ACTIVE_RESCUE_HOOK_ENFORCEMENT_V1"
if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

required = [
    "# QFOS_AGENT3_RESCUE_REENTRY_GUARD_V1",
    "orders = _agent3_filter_legacy_rescue_orders(orders, locals())",
]

for token in required:
    if token not in src:
        raise SystemExit(f"PATCH_FAILED: required token missing: {token}")

extension = r'''

# QFOS_AGENT3_ACTIVE_RESCUE_HOOK_ENFORCEMENT_V1
# Applies Agent 3 rescue checks at the direct allocator_rescue_hook path.
# Scope: rescue BUY admission only. No changes to exits, accounting, API,
# exposure limits, feature generation, or DB oversell protection.

def _qfos_agent3_rescue_feature_map(context):
    if not isinstance(context, dict):
        return {}

    for key in ("f_by_symbol", "features", "feature_map", "market_features"):
        value = context.get(key)
        if isinstance(value, dict):
            return value

    state = context.get("state")
    if isinstance(state, dict):
        value = state.get("features")
        if isinstance(value, dict):
            return value

    return {}

def _qfos_agent3_refresh_stop_loss_quarantines():
    """
    Rebuilds rescue quarantine truth from persisted stop-loss trades.
    1 recent stop => 30-minute cooldown from that stop.
    3+ stop losses in the past 2 hours => 2-hour loss-streak block
    from the latest stop.
    """
    refreshed = 0

    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT
                    symbol,
                    COUNT(*) AS stop_count,
                    MAX(created_at) AS last_stop_at
                FROM trades
                WHERE LOWER(side) = 'sell'
                  AND COALESCE(exit_reason, strategy, '') = 'sideways_stop_loss_exit'
                  AND created_at >= CURRENT_TIMESTAMP - interval '2 hours'
                GROUP BY symbol
            """)).mappings().all()

            for row in rows:
                symbol = str(row.get("symbol") or "").strip()
                last_stop_at = row.get("last_stop_at")
                stop_count = int(_qfos_agent3_rescue_float(row.get("stop_count")))

                if not symbol or last_stop_at is None:
                    continue

                if stop_count >= QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT:
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
                        :last_stop_at + (:minutes * interval '1 minute'),
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
                    "last_stop_at": last_stop_at,
                    "minutes": minutes,
                })

                refreshed += 1

        if refreshed:
            print(
                f"[RESCUE_STOP_LOSS_QUARANTINE_REFRESH] symbols={refreshed}",
                flush=True,
            )

    except Exception as exc:
        print(
            f"[RESCUE_STOP_LOSS_QUARANTINE_REFRESH_ERROR] error={exc!r}",
            flush=True,
        )

def _qfos_agent3_rescue_active_hook_gate(orders, context):
    """
    Final gate for the actual direct allocator rescue route.
    The existing Agent 3 re-entry guard now receives the allocator's
    feature map and the current regime from the rescue generator locals.
    """
    _qfos_agent3_refresh_stop_loss_quarantines()

    try:
        guarded = _qfos_agent3_rescue_reentry_guard(
            list(orders or []),
            context if isinstance(context, dict) else {},
        )
    except Exception as exc:
        print(
            f"[RESCUE_REJECT] symbol=UNKNOWN reason=active_hook_guard_error "
            f"error={exc!r}",
            flush=True,
        )
        return [
            order for order in list(orders or [])
            if not (
                isinstance(order, dict)
                and str(order.get("side") or "").lower() == "buy"
                and str(order.get("strategy") or order.get("reason") or "")
                    == "evo_allocator_rescue"
            )
        ]

    rescue_before = sum(
        1 for order in list(orders or [])
        if isinstance(order, dict)
        and str(order.get("side") or "").lower() == "buy"
        and str(order.get("strategy") or order.get("reason") or "")
            == "evo_allocator_rescue"
    )

    rescue_after = sum(
        1 for order in list(guarded or [])
        if isinstance(order, dict)
        and str(order.get("side") or "").lower() == "buy"
        and str(order.get("strategy") or order.get("reason") or "")
            == "evo_allocator_rescue"
    )

    print(
        f"[RESCUE_ACTIVE_HOOK_GATE] "
        f"rescue_before={rescue_before} rescue_after={rescue_after}",
        flush=True,
    )

    return guarded

'''

insert_anchor = "# QFOS_EXPECTANCY_EARLY_HOOK_START"
src = src.replace(insert_anchor, extension + "\n" + insert_anchor, 1)

old = "orders = _agent3_filter_legacy_rescue_orders(orders, locals())"
new = """orders = _agent3_filter_legacy_rescue_orders(orders, locals())
            orders = _qfos_agent3_rescue_active_hook_gate(orders, locals())"""

if old not in src:
    raise SystemExit("PATCH_FAILED: active rescue hook call not found")

src = src.replace(old, new, 1)
path.write_text(src, encoding="utf-8")

print("PATCH_WRITE_OK")
