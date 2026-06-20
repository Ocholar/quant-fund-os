import json
import urllib.request

raw = urllib.request.urlopen("http://127.0.0.1:8080/trades?limit=500", timeout=30).read().decode("utf-8")
payload = json.loads(raw)
if isinstance(payload, str):
    payload = json.loads(payload)

if isinstance(payload, dict):
    trades = payload.get("trades") or payload.get("latest_trades") or payload.get("value") or []
elif isinstance(payload, list):
    trades = payload
else:
    trades = []

latest_buy = next((t for t in trades if str(t.get("side")).lower() == "buy"), None)
latest_sell = next((t for t in trades if str(t.get("side")).lower() == "sell"), None)

print("trades_returned=", len(trades))
print("latest_buy_visible=", latest_buy is not None)
print("latest_buy_id_visible=", bool(latest_buy and latest_buy.get("id") is not None))
print("latest_buy_notional_visible=", bool(latest_buy and latest_buy.get("notional") is not None))
print("latest_buy_pnl_visible=", bool(latest_buy and latest_buy.get("pnl") is not None))

print("latest_sell_visible=", latest_sell is not None)
print("latest_sell_id_visible=", bool(latest_sell and latest_sell.get("id") is not None))
print("latest_sell_notional_visible=", bool(latest_sell and latest_sell.get("notional") is not None))
print("latest_sell_pnl_visible=", bool(latest_sell and latest_sell.get("pnl") is not None))
print("latest_sell_is_exit=", latest_sell.get("is_exit") if latest_sell else None)
print("latest_sell_exit_reason=", latest_sell.get("exit_reason") if latest_sell else None)

print("--- latest_buy ---")
print(json.dumps(latest_buy, indent=2, default=str))
print("--- latest_sell ---")
print(json.dumps(latest_sell, indent=2, default=str))
