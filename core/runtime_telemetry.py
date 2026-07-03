from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

KEY = "quantfund:runtime_telemetry"
TTL_SECONDS = 600

def _now():
    return datetime.now(timezone.utc).isoformat()

def _redis():
    try:
        import redis
        client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        return client
    except Exception:
        return None

def _read():
    client = _redis()
    if client is None:
        return {}
    try:
        raw = client.get(KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}

def _write(data):
    client = _redis()
    if client is None:
        return False
    try:
        client.set(KEY, json.dumps(data, sort_keys=True), ex=TTL_SECONDS)
        return True
    except Exception:
        return False

def _update(**values):
    data = _read()
    data.update(values)
    data["updated_at"] = _now()
    _write(data)
    return data

def runtime_start():
    data = _update(
        process_role="trading_loop",
        pid=os.getpid(),
        api_enabled=False,
        trading_loop_enabled=True,
        trading_loop_running=True,
        trading_loop_started_at=_now(),
        trading_loop_last_cycle_at=None,
        trading_loop_last_market_tick_at=None,
        trading_loop_last_feature_update_at=None,
        control_state_source="core.control/redis",
        cycle_id=0,
    )
    print(
        f"[QFOS_RUNTIME_AUTHORITY] process_role=trading_loop pid={os.getpid()} "
        "api_enabled=False trading_loop_enabled=True "
        "control_state_source=core.control/redis pause_state=unknown",
        flush=True,
    )
    print(
        f"[QFOS_LOOP_START] loop_id=main.py pid={os.getpid()} "
        "resume_state=unknown started_at=runtime_start",
        flush=True,
    )
    return data

def control_event(action, state_written):
    _update(
        last_control_event=action,
        last_control_event_at=_now(),
        control_state_source="api/core.control",
    )
    print(
        f"[QFOS_CONTROL_EVENT] action={action} "
        f"state_written={state_written} state_source=api/core.control",
        flush=True,
    )

def loop_control_observed(paused):
    _update(
        paused=bool(paused),
        loop_control_last_observed_at=_now(),
        control_state_source="core.control/redis",
    )
    action = "pause_observed" if paused else "resume_observed"
    print(
        f"[QFOS_LOOP_CONTROL] action={action} paused={bool(paused)} "
        "state_source=core.control/redis",
        flush=True,
    )

def cycle_from_locals(ctx):
    try:
        prices = ctx.get("prices") if isinstance(ctx.get("prices"), dict) else {}
        state = ctx.get("state") if isinstance(ctx.get("state"), dict) else {}
        features = (
            ctx.get("f_by_symbol")
            if isinstance(ctx.get("f_by_symbol"), dict)
            else state.get("features", {})
        )
        if not isinstance(features, dict):
            features = {}

        feature_values = list(features.values())
        feature_symbols = len(features)
        ready_features = sum(
            1 for item in feature_values
            if isinstance(item, dict) and bool(item.get("ready"))
        )
        normal_features = sum(
            1 for item in feature_values
            if isinstance(item, dict) and str(item.get("source", "")).upper() == "NORMAL"
        )

        raw_orders = len(ctx.get("raw_result_orders") or [])
        proposed_fills = len(ctx.get("proposed_fills") or [])
        persisted_fills = int(
            ctx.get("persisted_fills_count")
            if ctx.get("persisted_fills_count") is not None
            else len(ctx.get("persisted_fills") or [])
        )
        rejected_fills = int(ctx.get("rejected_fills") or 0)
        final_applied_fills = int(ctx.get("final_applied_fills") or persisted_fills)

        ranked = len(ctx.get("entry_quality_top_symbols") or [])
        broad = len(ctx.get("proposed_agent_orders") or [])
        rejected = len(ctx.get("entry_quality_rejections") or [])

        old = _read()
        cycle_id = int(old.get("cycle_id", 0) or 0) + 1
        now = _now()
        paused = bool(ctx.get("paused", False))

        _update(
            cycle_id=cycle_id,
            paused=paused,
            trading_loop_running=True,
            trading_loop_last_cycle_at=now,
            trading_loop_last_market_tick_at=now,
            trading_loop_last_feature_update_at=now,
            market_symbols=len(prices),
            valid_prices=len(prices),
            feature_symbols=feature_symbols,
            normal_features=normal_features,
            ready_features=ready_features,
            quality_candidates=ranked,
            raw_orders=raw_orders,
            proposed_fills=proposed_fills,
            persisted_fills=persisted_fills,
            rejected_fills=rejected_fills,
            final_applied_fills=final_applied_fills,
        )

        sample_symbol = next(iter(prices), "NA")
        sample_price = prices.get(sample_symbol, "NA") if prices else "NA"

        print(
            f"[QFOS_MARKET_TICK] cycle_id={cycle_id} symbols_received={len(prices)} "
            f"valid_prices={len(prices)} sample_symbol={sample_symbol} sample_price={sample_price}",
            flush=True,
        )
        print(
            f"[QFOS_FEATURE_CYCLE] cycle_id={cycle_id} feature_symbols={feature_symbols} "
            f"normal_features={normal_features} ready_features={ready_features} "
            "warming_features=0 contract_rejects=0",
            flush=True,
        )
        print(
            f"[QFOS_QUALITY_CYCLE] cycle_id={cycle_id} broad_candidates={broad} "
            f"ranked_candidates={ranked} rejected_candidates={rejected} top_count={ranked}",
            flush=True,
        )
        print(
            f"[QFOS_EXECUTION_CYCLE] cycle_id={cycle_id} raw_orders={raw_orders} "
            f"proposed_fills={proposed_fills} persisted_fills={persisted_fills} "
            f"rejected_fills={rejected_fills} final_applied_fills={final_applied_fills}",
            flush=True,
        )
        print(
            f"[QFOS_CYCLE] cycle_id={cycle_id} paused={paused} market_symbols={len(prices)} "
            f"valid_prices={len(prices)} feature_symbols={feature_symbols} "
            f"normal_features={normal_features} ready_features={ready_features} "
            f"quality_candidates={ranked} raw_orders={raw_orders} "
            f"proposed_fills={proposed_fills} persisted_fills={persisted_fills} "
            f"rejected_fills={rejected_fills} final_applied_fills={final_applied_fills} "
            f"updated_at={now}",
            flush=True,
        )
    except Exception as exc:
        print(f"[QFOS_TELEMETRY_ERROR] cycle_from_locals_error={exc}", flush=True)

def runtime_fields():
    data = _read()
    last = data.get("trading_loop_last_cycle_at")
    age = None

    if last:
        try:
            age = max(
                0.0,
                time.time() - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp(),
            )
        except Exception:
            age = None

    running = bool(data.get("trading_loop_running", False))
    stale = bool(running and (age is None or age > 90.0))

    return {
        "trading_loop_running": running,
        "trading_loop_last_cycle_at": data.get("trading_loop_last_cycle_at"),
        "trading_loop_last_market_tick_at": data.get("trading_loop_last_market_tick_at"),
        "trading_loop_last_feature_update_at": data.get("trading_loop_last_feature_update_at"),
        "trading_loop_heartbeat_age_seconds": age,
        "runtime_loop_stale": stale,
        "runtime_anomaly_warnings": ["trading_loop_heartbeat_stale"] if stale else [],
        "runtime_cycle_id": data.get("cycle_id", 0),
        "runtime_market_symbols": data.get("market_symbols", 0),
        "runtime_valid_prices": data.get("valid_prices", 0),
        "runtime_normal_features": data.get("normal_features", 0),
        "runtime_ready_features": data.get("ready_features", 0),
    }
