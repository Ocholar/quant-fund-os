from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "QFOS_AGENT5_EXEC_BRIDGE_MARKETDATA_ADAPTER_FIX_V1"
if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

old = '''            prices_obj = globals().get("prices", {}) or globals().get("market", {}) or {}
            equity_obj = float(getattr(portfolio, "equity", 100.0) or 100.0)
            ok, reason = can_buy(symbol, fill, prices_obj, equity_obj)'''

new = '''            # QFOS_AGENT5_EXEC_BRIDGE_MARKETDATA_ADAPTER_FIX_V1
            # can_buy() expects dict-like market data. The runtime may expose
            # PaperMarketData, which is not .get()-compatible and caused:
            # risk_gate_error:'PaperMarketData' object has no attribute 'get'
            # Use the already-normalized fill price as the authoritative mark
            # for this bridge validation call.
            prices_obj = {symbol: price}
            equity_obj = float(getattr(portfolio, "equity", 100.0) or 100.0)
            ok, reason = can_buy(symbol, fill, prices_obj, equity_obj)'''

if old not in text:
    raise SystemExit("ERROR: target can_buy market data block not found")

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
