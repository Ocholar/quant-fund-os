from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

# 1. Make hard-handoff builder aware that the main loop already updated
#    the FeatureStore for this cycle.
old_sig = '''def _qfos_agent4_build_normal_feature_map(features_obj, prices, settings):
    """Build a valid NORMAL feature map from real validated prices."""
'''
new_sig = '''def _qfos_agent4_build_normal_feature_map(
    features_obj,
    prices,
    settings,
    already_updated=False,
    prior_health=None,
):
    """Build a valid NORMAL feature map from real validated prices."""
'''

if old_sig not in src:
    raise SystemExit("PATCH_FAILED: Agent4 builder signature not found")

src = src.replace(old_sig, new_sig, 1)

old_primary = '''    feature_health = None
    built = {}

    # First try the runtime FeatureStore object already used by main.py.
    try:
        if features_obj is not None and hasattr(features_obj, "update"):
            feature_health = features_obj.update(clean_prices)
        if features_obj is not None and hasattr(features_obj, "all_features"):
'''

new_primary = '''    feature_health = prior_health
    built = {}

    # First try the runtime FeatureStore object already updated by main.py.
    # Do not append the same validated price map twice in one cycle:
    # duplicate append makes arr[-1] == arr[-2] and forces one_tick to 0.
    try:
        if (
            features_obj is not None
            and hasattr(features_obj, "update")
            and not already_updated
        ):
            feature_health = features_obj.update(clean_prices)
        if features_obj is not None and hasattr(features_obj, "all_features"):
'''

if old_primary not in src:
    raise SystemExit("PATCH_FAILED: duplicate primary FeatureStore update block not found")

src = src.replace(old_primary, new_primary, 1)

old_call = '''                f_by_symbol, feature_health, _agent4_feature_builder = _qfos_agent4_build_normal_feature_map(
                    features_obj=features,
                    prices=prices,
                    settings=settings,
                )
'''

new_call = '''                f_by_symbol, feature_health, _agent4_feature_builder = _qfos_agent4_build_normal_feature_map(
                    features_obj=features,
                    prices=prices,
                    settings=settings,
                    already_updated=True,
                    prior_health=feature_health,
                )
'''

if old_call not in src:
    raise SystemExit("PATCH_FAILED: main-loop hard-handoff call not found")

src = src.replace(old_call, new_call, 1)

# 2. Remove unsafe history_len>=4 ready override.
old_ready = '''        # If FeatureStore already marked it ready, preserve that.
        # If missing ready but required numeric/price fields exist, allow it as ready.
        if f.get("ready") is not True:
            history_len = int(_qfos_agent4_float(f.get("history_len"), 0.0))
            if history_len >= 4:
                f["ready"] = True

'''

new_ready = '''        # Preserve FeatureStore readiness exactly.
        # Warming features must not be promoted by a local history shortcut.

'''

if old_ready not in src:
    raise SystemExit("PATCH_FAILED: unsafe history readiness override not found")

src = src.replace(old_ready, new_ready, 1)

marker = "# QFOS_AGENT4_SINGLE_UPDATE_ONE_TICK_FIX_V1"
if marker not in src:
    anchor = "def _qfos_agent4_build_normal_feature_map("
    src = src.replace(anchor, marker + "\n" + anchor, 1)

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
