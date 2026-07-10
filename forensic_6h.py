import os
import re
import ast

log_file = "docker_logs_6h.txt"

if not os.path.exists(log_file):
    print(f"{log_file} not found.")
    exit(1)

with open(log_file, "r", encoding="utf-16", errors="ignore") as f:
    lines = f.readlines()

rejection_reasons = {}
total_rejected = 0
total_evaluated = 0

duplicate_sell_triggers = 0
exceptions = 0
top_n_triggers = 0

executions = []
current_cycle_top_signals = []
current_cycle_rejects = {}

for line in lines:
    line_lower = line.lower()
    
    # Track top signals
    if "top features/signals:" in line_lower:
        current_cycle_top_signals = []
        current_cycle_rejects = {}
    elif "source=normal" in line_lower and "strength=" in line_lower:
        #   GUA/USDT | source=NORMAL direction=None strength=0.0194 momentum=-0.004
        m = re.search(r"([A-Z]+/[A-Z]+)\s*\|\s*source=NORMAL.*?strength=([\d\.-]+)\s+momentum=([\d\.-]+)", line)
        if m:
            current_cycle_top_signals.append({
                "symbol": m.group(1),
                "strength": float(m.group(2)),
                "momentum": float(m.group(3))
            })
            
    # Track rejections
    if "[QUALITY_RANK_SUMMARY]" in line:
        # [QUALITY_RANK_SUMMARY] broad_candidates=114 allowed_candidates=0 rejected_candidates=114 reject_reason_counts={'signal_too_weak_0.0000_lt_0.0250': 48, ...}
        m_broad = re.search(r"broad_candidates=(\d+)", line)
        if m_broad:
            total_evaluated += int(m_broad.group(1))
            
        m_rej = re.search(r"rejected_candidates=(\d+)", line)
        if m_rej:
            total_rejected += int(m_rej.group(1))
            
        m_dict = re.search(r"reject_reason_counts=(\{.*?\})", line)
        if m_dict:
            try:
                counts = ast.literal_eval(m_dict.group(1))
                current_cycle_rejects = counts
                for k, v in counts.items():
                    # Group reasons
                    if "too_weak" in k:
                        reason = "Signal too weak"
                    elif "confidence" in k:
                        reason = "Confidence below threshold"
                    elif "momentum" in k:
                        reason = "Momentum mismatch"
                    elif "exposure" in k:
                        reason = "Exposure limit"
                    elif "quote" in k or "stable" in k:
                        reason = "Excluded quote/stable coin"
                    else:
                        reason = k
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + v
            except:
                pass
                
    # Track executions
    if "[EXECUTION_STAGE] can_buy" in line and "approved=True" in line:
        m = re.search(r"symbol=([A-Z]+/[A-Z]+)", line)
        if m:
            symbol = m.group(1)
            # find its strength in top signals
            exec_signal = next((s for s in current_cycle_top_signals if s["symbol"] == symbol), None)
            
            # find strongest rejected
            max_reject_strength = 0.0
            for k in current_cycle_rejects.keys():
                m_str = re.search(r"too_weak_([\d\.-]+)_lt_", k)
                if m_str:
                    val = float(m_str.group(1))
                    if val > max_reject_strength:
                        max_reject_strength = val
                        
            executions.append({
                "symbol": symbol,
                "exec_strength": exec_signal["strength"] if exec_signal else "N/A",
                "exec_momentum": exec_signal["momentum"] if exec_signal else "N/A",
                "max_reject_strength": max_reject_strength
            })

    if "duplicate sell" in line_lower:
        duplicate_sell_triggers += 1
    if "exception" in line_lower and "metrics reconciliation" not in line_lower and "dust_aware" not in line_lower and "exceptional signal" not in line_lower and "exceptional_evo" not in line_lower:
        exceptions += 1

print("--- Forensic 6H Log Analysis ---")
print(f"Total Evaluated Candidates: {total_evaluated}")
print(f"Total Rejected Candidates: {total_rejected}")
print(f"Duplicate Sell Guard Triggers: {duplicate_sell_triggers}")
print(f"Unhandled Exceptions: {exceptions}")

print("\n--- Rejection Reasons ---")
for k, v in sorted(rejection_reasons.items(), key=lambda item: item[1], reverse=True):
    pct = (v / total_rejected * 100) if total_rejected > 0 else 0
    print(f"{k}: {v} ({pct:.2f}%)")

print("\n--- Executions vs Strongest Rejected ---")
for e in executions:
    diff = "N/A"
    if e["exec_strength"] != "N/A" and e["max_reject_strength"] != 0.0:
        diff = e["exec_strength"] - e["max_reject_strength"]
    print(f"Executed: {e['symbol']} | Strength: {e['exec_strength']} | Strongest Rejected: {e['max_reject_strength']} | Diff: {diff}")
