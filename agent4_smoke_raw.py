from feature_store import FeatureStore

fs = FeatureStore()
for i in range(25):
    fs.update({
        "BTC/USDT": 60000 + i,
        "ETH/USDT": 3000 + (i * 0.5),
    })

health = fs.health_snapshot()
btc = fs.features("BTC/USDT")

print("RAW_INPUT_HEALTH", health)
print("BTC_FEATURE_READY", btc.get("ready"))
print("BTC_FEATURE_SOURCE", btc.get("source"))
print("BTC_FEATURE_CONFIDENCE_PRESENT", "confidence" in btc)

assert health["feature_history_symbols_count"] == 2
assert health["normal_feature_count"] == 2
assert btc["ready"] is True
assert btc["source"] == "NORMAL"
assert "confidence" in btc
print("RAW_INPUT_TEST_PASS")
