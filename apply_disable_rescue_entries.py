from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_DISABLE_RESCUE_BUYS_V1"

if marker in src:
    print("RESCUE_BLOCK_ALREADY_PRESENT")
    raise SystemExit(0)

anchor = 'if __name__ == "__main__":'

index = src.rfind(anchor)

if index < 0:
    raise SystemExit(
        "PATCH_FAILED: final __main__ anchor not found. No source changed."
    )

patch = r'''

# QFOS_DISABLE_RESCUE_BUYS_V1
# Temporary paper-safety containment:
# rescue allocation has negative raw-fill expectancy in the reconciled sample.
# SELLs remain permitted. Normal non-rescue BUY paths remain permitted.

_qfos_persist_fill_atomic_before_rescue_block = qfos_persist_fill_atomic

def qfos_persist_fill_atomic(conn, fill, source="main_loop"):
    try:
        if isinstance(fill, dict):
            _qfos_rescue_fill = fill
        else:
            _qfos_rescue_fill = {
                "side": getattr(fill, "side", None),
                "symbol": getattr(fill, "symbol", None),
                "strategy": getattr(fill, "strategy", None),
            }

        _qfos_rescue_side = str(
            _qfos_rescue_fill.get("side") or ""
        ).strip().lower()

        _qfos_rescue_strategy = str(
            _qfos_rescue_fill.get("strategy") or ""
        ).strip().lower()

        _qfos_rescue_source = str(source or "").strip().lower()

        is_rescue = (
            "evo_allocator_rescue" in _qfos_rescue_strategy
            or "allocator_rescue_hook" in _qfos_rescue_source
        )

        if _qfos_rescue_side == "buy" and is_rescue:
            print(
                "[RESCUE_BUY_BLOCK] "
                f"symbol={_qfos_rescue_fill.get('symbol')} "
                f"strategy={_qfos_rescue_fill.get('strategy')} "
                f"source={source} "
                "reason=negative_raw_fill_expectancy",
                flush=True,
            )
            return False

    except Exception as exc:
        # Do not fail open on an uncertain rescue BUY classification.
        try:
            if str(
                (fill.get("side") if isinstance(fill, dict) else getattr(fill, "side", ""))
                or ""
            ).strip().lower() == "buy":
                print(
                    f"[RESCUE_BUY_BLOCK] reason=guard_exception error={exc!r}",
                    flush=True,
                )
                return False
        except Exception:
            return False

    return _qfos_persist_fill_atomic_before_rescue_block(conn, fill, source)

'''

src = src[:index] + patch + "\n" + src[index:]
path.write_text(src, encoding="utf-8")

print("RESCUE_BLOCK_PATCH_OK")