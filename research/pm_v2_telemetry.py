import json
import math
import sys
from pathlib import Path
from collections import Counter
import statistics

def summarize_telemetry(log_file="logs/pm_v2/pm_v2_candidates.jsonl"):
    path = Path(log_file)
    if not path.exists():
        print(f"Log file {log_file} does not exist.")
        return

    replacements_proposed = 0
    incumbent_scores = []
    incoming_scores = []
    incumbent_ages = []
    targeted_symbols = Counter()
    rejection_reasons = Counter()
    seen_uuids = set()

    with path.open("r", encoding="utf-8") as f:
        for line_idx, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"ERROR: JSON malformed on line {line_idx}: {e}")
                sys.exit(1)
                
            if data.get("event") == "pm_v2_outcome":
                tuuid = data.get("trade_uuid")
                if tuuid is None:
                    print(f"ERROR: Missing trade_uuid on line {line_idx}")
                    sys.exit(1)
                if tuuid in seen_uuids:
                    print(f"ERROR: Duplicate trade_uuid {tuuid} on line {line_idx}")
                    sys.exit(1)
                seen_uuids.add(tuuid)
                continue

            if data.get("event") != "pm_v2_candidate":
                continue

            # Strict Field checks
            required_fields = ["candidate_time", "candidate_symbol", "candidate_rank", 
                               "candidate_score", "reason", "decision", "dry_run"]
            for field in required_fields:
                if field not in data:
                    print(f"ERROR: Missing required field '{field}' on line {line_idx}")
                    sys.exit(1)

            cand_score = data["candidate_score"]
            if cand_score is None or math.isnan(float(cand_score)):
                print(f"ERROR: NaN or None candidate_score on line {line_idx}")
                sys.exit(1)
                
            rejection_reasons[data.get("reason", "UNKNOWN")] += 1
            
            if data.get("decision") == "REPLACE":
                replacements_proposed += 1
                
                # Check incumbent specific fields for REPLACE decision
                inc_score = data.get("incumbent_score")
                inc_age = data.get("incumbent_age")
                if inc_score is None or math.isnan(float(inc_score)):
                    print(f"ERROR: NaN or None incumbent_score on line {line_idx}")
                    sys.exit(1)
                if inc_age is None or float(inc_age) < 0:
                    print(f"ERROR: Negative or missing incumbent_age on line {line_idx}")
                    sys.exit(1)

                incumbent_scores.append(inc_score)
                incoming_scores.append(cand_score)
                incumbent_ages.append(inc_age)
                targeted_symbols[data.get("incumbent_symbol", "UNKNOWN")] += 1

    print("--- PM V2 Dry-Run Telemetry Summary ---")
    print(f"Replacements proposed: {replacements_proposed}")
    
    if replacements_proposed > 0:
        avg_inc_score = statistics.mean(incumbent_scores)
        avg_new_score = statistics.mean(incoming_scores)
        avg_age = statistics.mean(incumbent_ages)
        print(f"Average incumbent score: {avg_inc_score:.4f}")
        print(f"Average incoming score: {avg_new_score:.4f}")
        print(f"Average age (minutes): {avg_age:.1f}")
        
        print("\nSymbols repeatedly targeted:")
        for sym, count in targeted_symbols.most_common(5):
            print(f"  {sym}: {count}")

    print("\nReasons rejected (total candidates evaluated):")
    for reason, count in rejection_reasons.most_common():
        print(f"  {reason}: {count}")
    
    print("\nValidation PASSED: No malformed JSON, duplicate UUIDs, or schema violations found.")

if __name__ == "__main__":
    summarize_telemetry()
