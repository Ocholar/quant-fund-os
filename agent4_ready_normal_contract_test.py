import os
os.environ["QFOS_FEATURE_HISTORY_PATH"] = "data/agent4_test_feature_history_runtime.json"
from feature_store import FeatureStore

fs = FeatureStore(window=120, min_history=8)

for i in range(10):
    fs.update({
        "prices": {
            "BTC/USDT": 60000 + i,
            "ETH/USDT": 3000 + (i * 0.5),
            "RAIN/USDT": 0.014 + (i * 0.00001),
        },
        "timestamp": 1234567890 + i,
        "source": "mexc_real_prices_only",
        "count": 3,
    })

health = fs.health_snapshot()
features = fs.all_features(["BTC/USDT", "ETH/USDT", "RAIN/USDT"])
ready = {s: f for s, f in features.items() if f.get("ready") is True and f.get("source") == "NORMAL"}

print("LOCAL_FEATURE_HEALTH", health)
print("LOCAL_READY_NORMAL_COUNT", len(ready))
print("LOCAL_SAMPLE", ready)

assert health["trusted_prices_count"] == 3
assert health["normal_feature_count"] >= 3
assert health["ready_feature_count"] >= 3
assert len(ready) >= 3
assert all(f.get("ready") is True for f in ready.values())
assert all(f.get("source") == "NORMAL" for f in ready.values())
assert all("confidence" in f for f in ready.values())
print("LOCAL_READY_NORMAL_FEATURESTORE_PASS")

