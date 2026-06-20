from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8")

marker = "# QFOS_NORMALIZED_NAMEERROR_DUST_SELL_FIX_V1"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

# Find qfos_persist_fill_atomic.
m = re.search(r"(?m)^def\s+qfos_persist_fill_atomic\s*\([^\)]*\):\s*$", src)
if not m:
    raise SystemExit("PATCH_FAILED: def qfos_persist_fill_atomic(...) not found")

insert_at = m.end()

patch = r'''
    # QFOS_NORMALIZED_NAMEERROR_DUST_SELL_FIX_V1
    # Guarantee `normalized` exists in this function scope before any downstream reference.
    # Also block tiny dust SELLs that can appear after DB clamp / float precision cleanup.
    try:
        if isinstance(fill, dict):
            normalized = dict(fill)
        else:
            normalized = {
                "symbol": getattr(fill, "symbol", None),
                "side": getattr(fill, "side", None),
                "quantity": getattr(fill, "quantity", getattr(fill, "qty", None)),
                "fill_price": getattr(fill, "fill_price", getattr(fill, "price", None)),
                "expected_price": getattr(fill, "expected_price", getattr(fill, "price", None)),
                "strategy": getattr(fill, "strategy", None),
                "exit_reason": getattr(fill, "exit_reason", getattr(fill, "reason", None)),
                "confidence": getattr(fill, "confidence", 1.0),
                "pnl": getattr(fill, "pnl", 0.0),
            }

        # Keep the original `fill` aligned with normalized for code paths below this point.
        fill = normalized

        _qfos_side = str(normalized.get("side") or "").lower()
        _qfos_symbol = str(normalized.get("symbol") or "")
        try:
            _qfos_qty = abs(float(normalized.get("quantity") or normalized.get("qty") or 0.0))
        except Exception:
            _qfos_qty = 0.0

        # Dust SELL guard:
        # The failed 60-min run showed TRIA sell qty=1.5258789e-05 causing normalized NameError.
        # This is not a real economic position close; it is float residue. Ignore safely.
        _qfos_dust_sell_qty = 0.0001
        if _qfos_side == "sell" and _qfos_qty > 0 and _qfos_qty <= _qfos_dust_sell_qty:
            print(
                f"[QFOS_DUST_SELL_REJECT] symbol={_qfos_symbol} qty={_qfos_qty:.12f} "
                f"threshold={_qfos_dust_sell_qty:.12f} source={source}",
                flush=True,
            )
            return False

    except Exception as _qfos_norm_exc:
        print(
            f"[QFOS_NORMALIZED_GUARD_ERROR] error={_qfos_norm_exc!r} source={source}",
            flush=True,
        )
        # Fail closed for malformed fills instead of crashing the loop.
        return False
'''

src = src[:insert_at] + "\n" + patch + src[insert_at:]

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
