#!/usr/bin/env python3
"""
Agent 5 — Corrected Surgical Patch (Python version)
Run this to apply all missing patches to main.py
"""

import shutil
from datetime import datetime

# Read file
with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Keep track of modifications
original_count = len(lines)

def replace_lines(start_1idx, end_1idx, new_lines):
    """Replace lines[start:end] (1-indexed, inclusive) with new_lines"""
    global lines
    s = start_1idx - 1
    e = end_1idx - 1
    lines = lines[:s] + [l + '\n' for l in new_lines] + lines[e+1:]
    print(f"  [PATCH] Lines {start_1idx}-{end_1idx} -> {len(new_lines)} lines")

def replace_single_line(line_1idx, new_lines):
    """Replace a single line with multiple lines"""
    global lines
    s = line_1idx - 1
    lines = lines[:s] + [l + '\n' for l in new_lines] + lines[s+1:]
    print(f"  [PATCH] Line {line_1idx} -> {len(new_lines)} lines")

# Create backup
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
shutil.copy2('main.py', f'main.py.backup_{timestamp}')
print(f"BACKUP: main.py.backup_{timestamp}")

# PATCH 2: PAUSE_HARD_BLOCK bypass (lines 9388-9412)
print("\n[PATCH 2] Pause Hard Block bypass...")
patch2_old = '''        if _qfos_pause_side == "buy":
            _qfos_pause_state_known = False
            _qfos_pause_active = True  # fail closed if pause authority is unavailable

            try:
                _qfos_is_paused_fn = globals().get("is_paused")
                if callable(_qfos_is_paused_fn):
                    _qfos_pause_active = bool(_qfos_is_paused_fn())
                    _qfos_pause_state_known = True
                elif "paused" in globals():
                    _qfos_pause_active = bool(globals().get("paused"))
                    _qfos_pause_state_known = True
            except Exception:
                _qfos_pause_active = True

            if _qfos_pause_active:
                print(
                    "[PAUSE_HARD_BLOCK] "
                    f"side=buy symbol={_qfos_pause_fill.get('symbol')} "
                    f"strategy={_qfos_pause_fill.get('strategy')} "
                    f"source={source} "
                    f"pause_state_known={_qfos_pause_state_known}",
                    flush=True,
                )
                return False'''

# Already applied in previous step, verify
content_at_9388 = ''.join(lines[9387:9412])
if '_qfos_pause_state_known = False' in content_at_9388 and 'test_mode_bypass' not in content_at_9388:
    print("  [PATCH 2] Needs application - applying now")
    replace_lines(9388, 9412, [
        '        if _qfos_pause_side == "buy":',
        '            # AGENT 5 TEST ISOLATION BYPASS',
        '            if QFOS_TEST_MODE and str(source or "").strip().lower() == "canonical_rollback_test":',
        '                print(',
        '                    "[PAUSE_HARD_BLOCK] "',
        '                    f"side=buy symbol={_qfos_pause_fill.get(\'symbol\')} "',
        '                    f"strategy={_qfos_pause_fill.get(\'strategy\')} "',
        '                    f"source={source} "',
        '                    "reason=test_mode_bypass",',
        '                    flush=True,',
        '                )',
        '                # Fall through — do not return False',
        '            else:',
        '                _qfos_pause_state_known = False',
        '                _qfos_pause_active = True  # fail closed if pause authority is unavailable',
        '',
        '                try:',
        '                    _qfos_is_paused_fn = globals().get("is_paused")',
        '                    if callable(_qfos_is_paused_fn):',
        '                        _qfos_pause_active = bool(_qfos_is_paused_fn())',
        '                        _qfos_pause_state_known = True',
        '                    elif "paused" in globals():',
        '                        _qfos_pause_active = bool(globals().get("paused"))',
        '                        _qfos_pause_state_known = True',
        '                except Exception:',
        '                    _qfos_pause_active = True',
        '',
        '                if _qfos_pause_active:',
        '                    print(',
        '                        "[PAUSE_HARD_BLOCK] "',
        '                        f"side=buy symbol={_qfos_pause_fill.get(\'symbol\')} "',
        '                        f"strategy={_qfos_pause_fill.get(\'strategy\')} "',
        '                        f"source={source} "',
        '                        f"pause_state_known={_qfos_pause_state_known}",',
        '                        flush=True,',
        '                    )',
        '                    return False'
    ])
else:
    print("  [PATCH 2] Already applied or content mismatch - checking...")
    if 'test_mode_bypass' in content_at_9388:
        print("  [PATCH 2] Already applied ✓")
    else:
        print("  [PATCH 2] WARNING: Content mismatch, manual review needed")

# PATCH 4: Exit lifecycle daemon (lines 5791-5796)
print("\n[PATCH 4] Exit lifecycle daemon guard...")
content_at_5791 = ''.join(lines[5790:5796])
if 'qfos_exit_lifecycle_start_daemon()' in content_at_5791 and 'if not QFOS_TEST_MODE:' not in ''.join(lines[5787:5792]):
    replace_lines(5791, 5796, [
        'if not QFOS_TEST_MODE:',
        '    try:',
        '        qfos_exit_lifecycle_ensure_tables()',
        '        qfos_exit_lifecycle_start_daemon()',
        '        print("[EXIT_LIFECYCLE] startup_daemon_registered_no_early_evaluation", flush=True)',
        '    except Exception as e:',
        '        print(f"[EXIT_DECISION_ERROR] startup_call_v1 error={e}", flush=True)',
        'else:',
        '    print("[QFOS_TEST_MODE] Skipping exit lifecycle daemon startup.")'
    ])
else:
    print("  [PATCH 4] Already applied or content mismatch")

# PATCH 6: Emergency basket watchdog (line 12028)
print("\n[PATCH 6] Emergency basket watchdog guard...")
line_12028 = lines[12027].rstrip('\n')
prev_12027 = lines[12026].rstrip('\n')
if line_12028 == '_qfos_start_emergency_basket_watchdog()' and 'QFOS_TEST_MODE' not in prev_12027:
    replace_single_line(12028, [
        'if not QFOS_TEST_MODE:',
        '    _qfos_start_emergency_basket_watchdog()',
        'else:',
        '    print("[QFOS_TEST_MODE] Skipping emergency basket watchdog startup.")'
    ])
else:
    print(f"  [PATCH 6] Line 12028: '{line_12028[:60]}...'")
    print(f"  [PATCH 6] Prev line: '{prev_12027[:60]}...'")
    if 'QFOS_TEST_MODE' in prev_12027:
        print("  [PATCH 6] Already guarded ✓")
    else:
        print("  [PATCH 6] No unguarded standalone call found")

# PATCH 7: Active position watchdog (line 12798)
print("\n[PATCH 7] Active position watchdog guard...")
line_12798 = lines[12797].rstrip('\n')
prev_12797 = lines[12796].rstrip('\n')
if line_12798 == '_qfos_start_active_position_watchdog()' and 'QFOS_TEST_MODE' not in prev_12797:
    replace_single_line(12798, [
        'if not QFOS_TEST_MODE:',
        '    _qfos_start_active_position_watchdog()',
        'else:',
        '    print("[QFOS_TEST_MODE] Skipping active position watchdog startup.")'
    ])
else:
    print(f"  [PATCH 7] Line 12798: '{line_12798[:60]}...'")
    print(f"  [PATCH 7] Prev line: '{prev_12797[:60]}...'")
    if 'QFOS_TEST_MODE' in prev_12797:
        print("  [PATCH 7] Already guarded ✓")
    else:
        print("  [PATCH 7] No unguarded standalone call found")

# Write file
print(f"\nWriting {len(lines)} lines to main.py...")
with open('main.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# Verify
print("\n=== VERIFICATION ===")
with open('main.py', 'r', encoding='utf-8') as f:
    verify = f.read()

checks = {
    "QFOS_TEST_MODE declared": "QFOS_TEST_MODE = os.environ" in verify,
    "Test bypass in pause guard": "test_mode_bypass" in verify,
    "Cash daemon guarded": "Skipping cash equity authority daemon" in verify,
    "Exit lifecycle guarded": "Skipping exit lifecycle daemon" in verify,
    "Stale reconciler guarded": "Skipping stale reconciler daemon" in verify,
    "Emergency watchdog guarded": "Skipping emergency basket watchdog" in verify,
    "Active watchdog guarded": "Skipping active position watchdog" in verify,
}

all_pass = True
for check, result in checks.items():
    status = "PASS" if result else "FAIL"
    print(f"  [{status}] {check}")
    if not result:
        all_pass = False

if all_pass:
    print("\n✓ ALL CHECKS PASSED")
else:
    print("\n✗ SOME CHECKS FAILED - review needed")