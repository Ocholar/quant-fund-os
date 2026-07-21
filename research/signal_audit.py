import json
import re
import statistics
from collections import Counter
from pathlib import Path

def parse_logs():
    # Looking at startup.log which had the most recent tail data we saw
    # We saw [QUALITY_RANK_SUMMARY] and [FEATURE_HANDOFF]
    
    logs = [Path('quant.log'), Path('startup.log')]
    
    quality_summaries = []
    feature_handoffs = []
    
    for log_path in logs:
        if not log_path.exists():
            continue
        try:
            with log_path.open('r', encoding='utf-16') as f:
                # To avoid memory issues with huge logs, we only parse the last 50000 lines
                from collections import deque
                lines = deque(f, maxlen=100000)
                
                for line in lines:
                    if '[QUALITY_RANK_SUMMARY]' in line:
                        quality_summaries.append(line)
                    elif '[FEATURE_HANDOFF]' in line:
                        feature_handoffs.append(line)
        except Exception as e:
            try:
                with log_path.open('r', encoding='utf-8') as f:
                    from collections import deque
                    lines = deque(f, maxlen=100000)
                    for line in lines:
                        if '[QUALITY_RANK_SUMMARY]' in line:
                            quality_summaries.append(line)
                        elif '[FEATURE_HANDOFF]' in line:
                            feature_handoffs.append(line)
            except Exception as e2:
                print(f"Error reading {log_path}: {e2}")

    return quality_summaries, feature_handoffs

def audit():
    quality_summaries, feature_handoffs = parse_logs()
    
    print(f"Found {len(quality_summaries)} QUALITY_RANK_SUMMARY lines")
    print(f"Found {len(feature_handoffs)} FEATURE_HANDOFF lines")
    
    # Task 1 & 3: Rejection pipeline and thresholds
    rejection_reasons = Counter()
    total_candidates = 0
    allowed = 0
    rejected = 0
    
    # regex to parse dict-like string from log: reject_reason_counts={'signal_too_weak_0.0004_lt_0.0250': 12, ...}
    for qs in quality_summaries[-500:]:
        m = re.search(r'broad_candidates=(\d+)', qs)
        if m:
            total_candidates += int(m.group(1))
        
        m2 = re.search(r'allowed_candidates=(\d+)', qs)
        if m2:
            allowed += int(m2.group(1))
            
        m3 = re.search(r'rejected_candidates=(\d+)', qs)
        if m3:
            rejected += int(m3.group(1))
            
        m4 = re.search(r"reject_reason_counts=({[^}]+})", qs)
        if m4:
            try:
                # convert single quotes to double quotes for json parsing
                jstr = m4.group(1).replace("'", '"')
                counts = json.loads(jstr)
                for k, v in counts.items():
                    rejection_reasons[k] += v
            except:
                pass
                
    print("--- Pipeline ---")
    print(f"Total evaluated: {total_candidates}")
    print(f"Allowed: {allowed}")
    print(f"Rejected: {rejected}")
    print("\nTop 10 Rejection Reasons:")
    for k, v in rejection_reasons.most_common(10):
        print(f"  {k}: {v}")

    # Task 2, 4 & 7: Score distributions, features, signal decomposition
    scores = []
    confidences = []
    trends = []
    momentums = []
    
    top_candidates = []
    
    for fh in feature_handoffs[-100:]:
        # extract sample=[...] array
        m = re.search(r'sample=(\[.*?\])$', fh)
        if m:
            try:
                jstr = m.group(1).replace("'", '"').replace("False", "false").replace("True", "true").replace("None", "null")
                sample = json.loads(jstr)
                for item in sample:
                    sig = item.get('signal_strength', 0.0)
                    conf = item.get('confidence', 0.0)
                    trend = item.get('trend', 0.0)
                    mom = item.get('momentum', 0.0)
                    
                    scores.append(sig)
                    confidences.append(conf)
                    trends.append(trend)
                    momentums.append(mom)
                    
                    top_candidates.append({
                        "symbol": item.get('symbol'),
                        "trend": trend,
                        "momentum": mom,
                        "signal_strength": sig,
                        "confidence": conf,
                        "score_before_filters": sig # proxy
                    })
            except Exception as e:
                pass
                
    if scores:
        print("\n--- Distributions ---")
        scores.sort()
        print(f"Scores (signal_strength): N={len(scores)}")
        print(f"Min: {scores[0]:.6f}")
        print(f"Med: {scores[len(scores)//2]:.6f}")
        print(f"Mean: {sum(scores)/len(scores):.6f}")
        print(f"P90: {scores[int(len(scores)*0.9)]:.6f}")
        print(f"P95: {scores[int(len(scores)*0.95)]:.6f}")
        print(f"P99: {scores[int(len(scores)*0.99)]:.6f}")
        print(f"Max: {scores[-1]:.6f}")
        
    if confidences:
        confidences.sort()
        print(f"\nConfidences: N={len(confidences)}")
        print(f"Min: {confidences[0]:.6f}")
        print(f"Med: {confidences[len(confidences)//2]:.6f}")
        print(f"Mean: {sum(confidences)/len(confidences):.6f}")
        print(f"P99: {confidences[int(len(confidences)*0.99)]:.6f}")
        print(f"Max: {confidences[-1]:.6f}")
        
    print("\n--- Top 10 Candidates (from samples) ---")
    top_candidates.sort(key=lambda x: x['signal_strength'], reverse=True)
    for c in top_candidates[:10]:
        print(c)

if __name__ == "__main__":
    audit()
