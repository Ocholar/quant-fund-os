import os
os.environ["QFOS_FEATURE_HISTORY_PATH"] = "data/agent4_test_feature_history_runtime.json"
from feature_store import FeatureStore

fs = FeatureStore()

for i in range(25):
    fs.update({
        "prices": {
            "BTC/USDT": 60000 + i,
            "ETH/USDT": 3000 + (i * 0.5),
        },
        "timestamp": 1234567890 + i,
        "source": "mexc_real_prices_only",
        "count": 2,
    })

health = fs.health_snapshot()
features = fs.all_features()

print("LOCAL_FEATURESTORE_HEALTH", health)
print("LOCAL_FEATURE_KEYS", {k: sorted(v.keys()) for k, v in features.items()})
print("LOCAL_READY_COUNT", sum(1 for v in features.values() if v.get("ready") is True))
print("LOCAL_NORMAL_COUNT", sum(1 for v in features.values() if v.get("source") == "NORMAL"))
print("LOCAL_CONFIDENCE_COUNT", sum(1 for v in features.values() if "confidence" in v))

assert health["normal_feature_count"] > 0
assert health["ready_feature_count"] > 0
assert all(v.get("source") == "NORMAL" for v in features.values())
assert all(v.get("ready") is True for v in features.values())
assert all("confidence" in v for v in features.values())

print("LOCAL_FEATURESTORE_CONTRACT_PASS")

