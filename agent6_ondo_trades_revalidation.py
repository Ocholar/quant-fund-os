import json
import urllib.request
import sys

url = "http://127.0.0.1:8080/trades?limit=500"

raw = urllib.request.urlopen(url, timeout=20).read().decode("utf-8")

# /trades may currently be double-encoded by old API normalizer layers.
payload = json.loads(raw)
if isinstance(payload, str):
    payload = json.loads(payload)

if isinstance(payload, dict):
    if isinstance(payload.get("trades"), list):
        trades = payload["trades"]
    elif isinstance(payload.get("latest_trades"), list):
        trades = payload["latest_trades"]
    elif isinstance(payload.get("value"), list):
        trades = payload["value"]
    else:
        trades = []
elif isinstance(payload, list):
    trades = payload
else:
    trades = []

ondo_buys = [
    t for t in trades
    if str(t.get("symbol")) == "ONDO/USDT" and str(t.get("side")).lower() == "buy"
]
ondo_sells = [
    t for t in trades
    if str(t.get("symbol")) == "ONDO/USDT" and str(t.get("side")).lower() == "sell"
]

ondo_buys.sort(key=lambda x: int(x.get("id") or 0), reverse=True)
ondo_sells.sort(key=lambda x: int(x.get("id") or 0), reverse=True)

ondo_buy = ondo_buys[0] if ondo_buys else None
ondo_sell = ondo_sells[0] if ondo_sells else None

print("\n--- API parse summary ---")
print("total_trades_returned=", len(trades))
print("first_id=", trades[0].get("id") if trades else None)
print("last_id=", trades[-1].get("id") if trades else None)

print("\n--- ONDO BUY ---")
print(json.dumps(ondo_buy, indent=2, default=str))

print("\n--- ONDO SELL ---")
print(json.dumps(ondo_sell, indent=2, default=str))

checks = {
    "ondo_buy_visible": ondo_buy is not None,
    "ondo_sell_visible": ondo_sell is not None,
    "ondo_sell_is_exit_visible": bool(ondo_sell and ondo_sell.get("is_exit") is True),
    "ondo_sell_exit_reason_visible": bool(ondo_sell and ondo_sell.get("exit_reason") == "sideways_take_profit_exit"),
    "ondo_sell_pnl_visible": bool(ondo_sell and ondo_sell.get("pnl") is not None),
    "ondo_sell_id_visible": bool(ondo_sell and ondo_sell.get("id") is not None),
    "ondo_sell_notional_visible": bool(ondo_sell and ondo_sell.get("notional") is not None),
}

for k, v in checks.items():
    print(f"{k}={v}")

if ondo_sell:
    print("ondo_sell_id=", ondo_sell.get("id"))
    print("ondo_sell_pnl=", ondo_sell.get("pnl"))
    print("ondo_sell_notional=", ondo_sell.get("notional"))
    print("ondo_sell_exit_reason=", ondo_sell.get("exit_reason"))

if all(checks.values()):
    print("\nAGENT6_ONDO_TRADES_REVALIDATION: PASS")
    sys.exit(0)
else:
    print("\nAGENT6_ONDO_TRADES_REVALIDATION: FAIL")
    sys.exit(1)
