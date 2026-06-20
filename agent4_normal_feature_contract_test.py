import os
os.environ["QFOS_FEATURE_HISTORY_PATH"] = "data/agent4_test_feature_history_runtime.json"
from feature_store import FeatureStore

fs = FeatureStore(window=120, min_history=8)

for i in range(10):
    fs.update({
        "prices": {
            "BTC/USDT": 60000 + i,
            "ETH/USDT": 3000 + (i * 0.5),
            "ASTER/USDT": 0.65 + (i * 0.0003),
        },
        "timestamp": 1234567890 + i,
        "source": "mexc_real_prices_only",
        "count": 3,
    })

features = fs.all_features(["BTC/USDT", "ETH/USDT", "ASTER/USDT"])
health = fs.health_snapshot()

required = [
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
]

ready_normal = {}
for symbol, feature in features.items():
    if feature.get("ready") is True and feature.get("source") == "NORMAL":
        for key in required:
            assert key in feature, (symbol, key, feature)
        assert float(feature["price"]) > 0
        assert isinstance(float(feature["confidence"]), float)
        assert isinstance(float(feature["signal_strength"]), float)
        ready_normal[symbol] = feature

print("LOCAL_HEALTH", health)
print("LOCAL_READY_NORMAL_COUNT", len(ready_normal))
print("LOCAL_SAMPLE", ready_normal)
assert len(ready_normal) == 3
assert health["normal_feature_count"] >= 3
assert health["ready_feature_count"] >= 3
print("LOCAL_FEATURE_CONTRACT_PASS")

