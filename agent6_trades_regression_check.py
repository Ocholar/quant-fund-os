import json
import urllib.request

raw = urllib.request.urlopen("http://127.0.0.1:8080/trades?limit=50", timeout=30).read().decode("utf-8")
payload = json.loads(raw)
if isinstance(payload, str):
    payload = json.loads(payload)

if isinstance(payload, dict):
    trades = payload.get("trades") or payload.get("latest_trades") or payload.get("value") or []
elif isinstance(payload, list):
    trades = payload
else:
    trades = []

latest = trades[0] if trades else {}
print("trades_responds=", bool(trades))
print("latest_trade_id_visible=", latest.get("id") is not None)
print("latest_trade_pnl_visible=", latest.get("pnl") is not None)
print("latest_trade_notional_visible=", latest.get("notional") is not None)
print("latest_trade_symbol=", latest.get("symbol"))
print("latest_trade_side=", latest.get("side"))
print("latest_trade_id=", latest.get("id"))
print("latest_trade_pnl=", latest.get("pnl"))
print("latest_trade_notional=", latest.get("notional"))
