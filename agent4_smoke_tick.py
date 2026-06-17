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
eth = fs.features("ETH/USDT")

print("TICK_OBJECT_HEALTH", health)
print("ETH_FEATURE_READY", eth.get("ready"))
print("ETH_FEATURE_SOURCE", eth.get("source"))
print("ETH_FEATURE_CONFIDENCE_PRESENT", "confidence" in eth)

assert health["market_symbols_count"] == 2
assert health["trusted_prices_count"] == 2
assert health["feature_history_symbols_count"] == 2
assert health["normal_feature_count"] == 2
assert health["ready_feature_count"] == 2
assert eth["ready"] is True
assert eth["source"] == "NORMAL"
assert "confidence" in eth
print("TICK_OBJECT_TEST_PASS")
