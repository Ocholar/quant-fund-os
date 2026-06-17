from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

block_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    flags=re.S,
)

m = block_re.search(s)
if not m:
    raise SystemExit("FAIL: QFOS atomic persistence block not found")

block = m.group(0)

helper = r'''

def _qfos_exit_accounting_fields(fill, side, strategy, source):
    """
    Phase 3A accounting invariant.

    Any SELL in spot paper mode is a reduction/exit, not a fresh entry.
    Therefore it must be persisted with:
      is_exit = true
      exit_reason populated

    BUY rows remain non-exit unless explicitly supplied otherwise.
    """
    side = str(side or "").lower()
    strategy = str(strategy or "").strip()
    source = str(source or "").strip()

    raw_is_exit = fill.get("is_exit", None)
    raw_exit_reason = fill.get("exit_reason", None)

    if side == "sell":
        reason = (
            str(raw_exit_reason).strip()
            if raw_exit_reason not in (None, "", "None")
            else ""
        )

        if not reason:
            reason = strategy or source or "paper_sell_exit"

        return True, reason

    # BUY should not be counted as exit by default.
    if raw_is_exit in (True, 1, "1", "true", "True", "yes", "YES"):
        reason = (
            str(raw_exit_reason).strip()
            if raw_exit_reason not in (None, "", "None")
            else "explicit_buy_exit_flag"
        )
        return True, reason

    return False, None


def _qfos_assert_sell_exit_accounting(normalized_fill):
    side = str(normalized_fill.get("side") or "").lower()
    if side != "sell":
        return

    is_exit = normalized_fill.get("is_exit")
    exit_reason = normalized_fill.get("exit_reason")

    if not is_exit:
        raise RuntimeError(
            "SELL_ACCOUNTING_INVARIANT_FAIL:is_exit_false:%s"
            % normalized_fill.get("symbol")
        )

    if exit_reason in (None, "", "None"):
        raise RuntimeError(
            "SELL_ACCOUNTING_INVARIANT_FAIL:missing_exit_reason:%s"
            % normalized_fill.get("symbol")
        )
'''

if "def _qfos_exit_accounting_fields(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found inside atomic block")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted Phase 3A exit-accounting helpers.")
else:
    print("Exit-accounting helpers already present.")

# Add normalized is_exit/exit_reason fields.
if "exit_is_exit, exit_reason = _qfos_exit_accounting_fields(" not in block:
    old = '''        normalized = dict(fill)
        normalized.update({'''
    new = '''        exit_is_exit, exit_reason = _qfos_exit_accounting_fields(
            fill=fill,
            side=side,
            strategy=strategy,
            source=source,
        )

        normalized = dict(fill)
        normalized.update({'''
    if old not in block:
        raise SystemExit("FAIL: could not find normalized update insertion point")
    block = block.replace(old, new, 1)
    print("Inserted exit-accounting normalization call.")
else:
    print("Exit-accounting normalization call already present.")

# Add normalized fields inside normalized.update dict.
if '"is_exit": bool(exit_is_exit),' not in block:
    anchor = '''            "source": str(source),'''
    replacement = '''            "source": str(source),
            "is_exit": bool(exit_is_exit),
            "exit_reason": exit_reason,'''
    if anchor not in block:
        raise SystemExit("FAIL: could not insert is_exit/exit_reason into normalized fill")
    block = block.replace(anchor, replacement, 1)
    print("Inserted is_exit/exit_reason into normalized fill.")
else:
    print("Normalized is_exit/exit_reason already present.")

# Assert before trade insertion.
if "_qfos_assert_sell_exit_accounting(normalized)" not in block:
    anchor = '''        _qfos_upsert_position_atomic('''
    replacement = '''        _qfos_assert_sell_exit_accounting(normalized)

        _qfos_upsert_position_atomic('''
    if anchor not in block:
        raise SystemExit("FAIL: could not insert SELL accounting assertion")
    block = block.replace(anchor, replacement, 1)
    print("Inserted SELL accounting assertion before persistence.")
else:
    print("SELL accounting assertion already present.")

# Add is_exit/exit_reason to trade insert data map.
if '"is_exit": _qfos_bool_int(normalized_fill.get("is_exit", False)),' not in block:
    anchor = '''        "source": normalized_fill.get("source", "unknown"),'''
    replacement = '''        "source": normalized_fill.get("source", "unknown"),
        "is_exit": _qfos_bool_int(normalized_fill.get("is_exit", False)),
        "exit_reason": normalized_fill.get("exit_reason"),'''
    if anchor not in block:
        raise SystemExit("FAIL: could not insert is_exit/exit_reason into trade insert data")
    block = block.replace(anchor, replacement, 1)
    print("Inserted is_exit/exit_reason into trade insert data map.")
else:
    print("Trade insert is_exit/exit_reason already present.")

s = s[:m.start()] + block + s[m.end():]
p.write_text(s, encoding="utf-8")
print("Phase 3A execution/accounting patch complete.")
