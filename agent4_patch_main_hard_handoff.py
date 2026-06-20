from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

helper = r'''

# BEGIN AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2
_QFOS_AGENT4_DEDICATED_FEATURE_STORE = None


def _qfos_agent4_runtime_prices(raw_prices):
    """Return a clean symbol->price map from either raw prices or tick object."""
    if not isinstance(raw_prices, dict):
        return {}
    nested = raw_prices.get("prices")
    if isinstance(nested, dict):
        return nested
    return raw_prices


def _qfos_agent4_get_dedicated_feature_store():
    """Dedicated FeatureStore used only if main's feature object is stale/wrong."""
    global _QFOS_AGENT4_DEDICATED_FEATURE_STORE
    if _QFOS_AGENT4_DEDICATED_FEATURE_STORE is None:
        try:
            from feature_store import FeatureStore
            _QFOS_AGENT4_DEDICATED_FEATURE_STORE = FeatureStore()
            print("[FEATURE_HANDOFF] dedicated_feature_store_created=True", flush=True)
        except Exception as exc:
            print(f"[FEATURE_HANDOFF_ERROR] dedicated_store_create_failed={repr(exc)}", flush=True)
            return None
    return _QFOS_AGENT4_DEDICATED_FEATURE_STORE


def _qfos_agent4_is_contract_ready_normal(feature):
    if not isinstance(feature, dict):
        return False
    if feature.get("ready") is not True:
        return False
    if str(feature.get("source", "")).upper() != "NORMAL":
        return False
    try:
        if float(feature.get("price", 0.0) or 0.0) <= 0:
            return False
    except Exception:
        return False

    required = (
        "price",
        "trend",
        "long_trend",
        "volatility",
        "momentum",
        "one_tick_momentum",
        "signal_strength",
        "confidence",
        "symbol_regime",
        "breakout_score",
        "trend_quality",
        "is_symbol_uptrend",
        "is_choppy",
        "source",
        "ready",
    )

    for key in required:
        if key not in feature:
            return False

    for key in (
        "trend",
        "long_trend",
        "volatility",
        "momentum",
        "one_tick_momentum",
        "signal_strength",
        "confidence",
        "breakout_score",
        "trend_quality",
    ):
        try:
            float(feature.get(key))
        except Exception:
            return False

    return True


def _qfos_agent4_contract_repair_feature_map(feature_map):
    """Repair metadata only on already real, price-bearing NORMAL features."""
    if not isinstance(feature_map, dict):
        return {}

    out = {}

    def _flt(v, default=0.0):
        try:
            x = float(v)
            if x == x and x not in (float("inf"), float("-inf")):
                return x
        except Exception:
            pass
        return float(default)

    for symbol, feature in feature_map.items():
        if not isinstance(feature, dict):
            out[symbol] = feature
            continue

        f = dict(feature)

        if str(f.get("source", "NORMAL")).upper() == "RAW_MOMENTUM_FALLBACK":
            out[symbol] = f
            continue

        price = _flt(f.get("price"), 0.0)
        if price <= 0:
            out[symbol] = f
            continue

        f["source"] = "NORMAL"

        for k in (
            "trend",
            "long_trend",
            "volatility",
            "momentum",
            "one_tick_momentum",
            "signal_strength",
            "breakout_score",
            "trend_quality",
        ):
            f[k] = _flt(f.get(k), 0.0)

        if f.get("confidence") is None or str(f.get("confidence")).lower() in ("", "none", "nan"):
            signal = _flt(f.get("signal_strength"), 0.0)
            quality = _flt(f.get("trend_quality"), 0.0)
            breakout = _flt(f.get("breakout_score"), 0.0)
            f["confidence"] = max(0.0, min(1.0, (signal + quality + breakout) / 0.018))
        else:
            f["confidence"] = _flt(f.get("confidence"), 0.0)

        if not f.get("symbol_regime"):
            f["symbol_regime"] = "SYMBOL_NEUTRAL"

        if "is_symbol_uptrend" not in f:
            f["is_symbol_uptrend"] = str(f.get("symbol_regime", "")).upper() in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP")

        if "is_choppy" not in f:
            f["is_choppy"] = str(f.get("symbol_regime", "")).upper() == "SYMBOL_CHOPPY"

        if f.get("ready") is not True:
            try:
                history_len = int(float(f.get("history_len", 0) or 0))
            except Exception:
                history_len = 0
            if history_len >= 4:
                f["ready"] = True

        out[symbol] = f

    return out


def _qfos_agent4_count_ready_normal(feature_map):
    if not isinstance(feature_map, dict):
        return 0
    return sum(1 for f in feature_map.values() if _qfos_agent4_is_contract_ready_normal(f))


def _qfos_agent4_build_normal_feature_map(features_obj, prices, settings):
    """Build a valid NORMAL feature map from real validated prices."""
    clean_prices = _qfos_agent4_runtime_prices(prices)
    symbols = list(getattr(settings, "symbol_list", []) or [])

    feature_health = None
    built = {}

    # First try the runtime FeatureStore object already used by main.py.
    try:
        if features_obj is not None and hasattr(features_obj, "update"):
            feature_health = features_obj.update(clean_prices)
        if features_obj is not None and hasattr(features_obj, "all_features"):
            built = features_obj.all_features(symbols)
        elif features_obj is not None and hasattr(features_obj, "features"):
            built = {s: features_obj.features(s) for s in symbols}
    except Exception as exc:
        print(f"[FEATURE_HANDOFF_ERROR] primary_feature_build_failed={repr(exc)}", flush=True)
        built = {}

    built = _qfos_agent4_contract_repair_feature_map(built)
    if _qfos_agent4_count_ready_normal(built) > 0:
        return built, feature_health, "primary_feature_store"

    # If main's feature object is stale/wrong, use a dedicated FeatureStore.
    # This still uses only real validated prices; it does not create synthetic features.
    try:
        store = _qfos_agent4_get_dedicated_feature_store()
        if store is not None:
            feature_health = store.update(clean_prices)
            if hasattr(store, "all_features"):
                built = store.all_features(symbols)
            else:
                built = {s: store.features(s) for s in symbols}
            built = _qfos_agent4_contract_repair_feature_map(built)
            return built, feature_health, "dedicated_feature_store"
    except Exception as exc:
        print(f"[FEATURE_HANDOFF_ERROR] dedicated_feature_build_failed={repr(exc)}", flush=True)

    return built, feature_health, "failed_or_warming"


def _qfos_agent4_log_feature_handoff(feature_map, feature_health, source):
    try:
        ready_normal = {
            s: f for s, f in (feature_map or {}).items()
            if _qfos_agent4_is_contract_ready_normal(f)
        }
        ready_any = {
            s: f for s, f in (feature_map or {}).items()
            if isinstance(f, dict) and f.get("ready") is True
        }

        sample = []
        for s, f in list(ready_normal.items())[:3]:
            sample.append({
                "symbol": s,
                "price": f.get("price"),
                "trend": f.get("trend"),
                "long_trend": f.get("long_trend"),
                "volatility": f.get("volatility"),
                "momentum": f.get("momentum"),
                "one_tick_momentum": f.get("one_tick_momentum"),
                "signal_strength": f.get("signal_strength"),
                "confidence": f.get("confidence"),
                "symbol_regime": f.get("symbol_regime"),
                "breakout_score": f.get("breakout_score"),
                "trend_quality": f.get("trend_quality"),
                "is_symbol_uptrend": f.get("is_symbol_uptrend"),
                "is_choppy": f.get("is_choppy"),
                "source": f.get("source"),
                "ready": f.get("ready"),
            })

        print(
            "[FEATURE_HANDOFF] "
            f"Feature symbols={len(feature_map or {})} "
            f"normal_features={len(ready_normal)} "
            f"ready_features={len(ready_any)} "
            f"builder={source} "
            f"health={feature_health} "
            f"sample={sample}",
            flush=True,
        )
    except Exception as exc:
        print(f"[FEATURE_HANDOFF_ERROR] hard_log_failed={repr(exc)}", flush=True)
# END AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2
'''

if "BEGIN AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2" not in text:
    marker = "def _qfos_agent4_float"
    if marker in text:
        text = text.replace(marker, helper + "\n\n" + marker, 1)
    else:
        # Append near end if no stable marker exists.
        text = text + "\n\n" + helper + "\n"
    print("Inserted AGENT4 hard feature handoff helpers")
else:
    print("AGENT4 hard feature handoff helpers already present")

# Insert hard rebuild immediately before feature snapshot persistence if possible.
handoff = r'''
            # AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2
            try:
                f_by_symbol, feature_health, _agent4_feature_builder = _qfos_agent4_build_normal_feature_map(
                    features_obj=features,
                    prices=prices,
                    settings=settings,
                )
                _qfos_agent4_log_feature_handoff(f_by_symbol, feature_health, _agent4_feature_builder)
            except Exception as _agent4_hard_feature_error:
                print(f"[FEATURE_HANDOFF_ERROR] hard_rebuild_failed={repr(_agent4_hard_feature_error)}", flush=True)
'''

if "AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2" not in text.split("def _qfos_agent4_runtime_prices", 1)[-1]:
    # This branch is unlikely due helper marker; use direct marker below instead.
    pass

if "AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2\n            try:" not in text:
    marker = "            try:\n                _qfos_v2_upsert_feature_snapshot(f_by_symbol)"
    if marker in text:
        text = text.replace(marker, handoff + "\n" + marker, 1)
        print("Inserted hard handoff before feature snapshot")
    else:
        marker2 = "            ready = [f for f in f_by_symbol.values() if isinstance(f, dict) and f.get('ready')]"
        if marker2 in text:
            text = text.replace(marker2, handoff + "\n" + marker2, 1)
            print("Inserted hard handoff before ready-list")
        else:
            print("WARNING: Could not find feature snapshot/ready marker. Manual inspection needed.")
else:
    print("Hard handoff already inserted")

path.write_text(text, encoding="utf-8")
