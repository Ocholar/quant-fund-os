import os
import json
import pandas as pd
from collections import defaultdict
import glob

print("============================================================")
print("TASK E1.2 - CANDIDATE SELECTION AUDIT")
print("============================================================")

# 1. Load the trades to get their PnL
# We will just map symbol+timestamp (or trade_uuid if available in candidate logs) to PnL.
# Actually, the easiest is to map `candidate_id` to PnL if they are linked.
# Let's load feature_dataset.csv which has trade_uuid and symbol.
trades_df = pd.read_csv('research/feature_dataset.csv')
pnl_map = {} # candidate_id -> pnl
trade_uuid_to_pnl = dict(zip(trades_df['trade_uuid'], trades_df['pnl']))

# But candidate logs might not directly have PnL. Let's parse the candidates log to see what's there.
# We will parse one of the logs, e.g., candidates_2026-07-16.jsonl, and trades_2026-07-16.jsonl to link candidate_id to trade_id and then to PnL?
# Wait, the simplest way is to see which ranks were APPROVED and what their PnL was.
# Actually, the user asks "What if #2 had been taken?" meaning we want to evaluate the *predictive power* of the rank.
# If #2 wasn't taken, we don't have its exact PnL. But maybe it was taken in a nearby cycle?
# Let's just aggregate the PnL of all trades by their original cycle rank.

log_files = glob.glob('logs/candidates/candidates_*.jsonl')

rank_pnl = defaultdict(list)

# We also have logs/trades/trades_*.jsonl which contains TRADE_EXITED with realized_pnl.
# Let's build candidate_id -> realized_pnl mapping from trades logs.
candidate_pnl = {}
trade_logs = glob.glob('logs/trades/trades_*.jsonl')
for log_file in trade_logs:
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            if 'trade_exited' in line:
                try:
                    data = json.loads(line)
                    if data['event_type'] == 'trade_exited':
                        payload = data['payload']
                        cid = payload.get('candidate_id')
                        pnl = payload.get('realized_pnl')
                        if cid and pnl is not None:
                            candidate_pnl[cid] = pnl
                except:
                    pass

print(f"Loaded {len(candidate_pnl)} exited trades from trades logs.")

# Now parse candidate logs to get rank of each candidate_id
rank_counts = defaultdict(int)
for log_file in log_files:
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            if 'candidate_ranked' in line:
                try:
                    data = json.loads(line)
                    if data['event_type'] == 'candidate_ranked':
                        payload = data['payload']
                        cid = payload.get('candidate_id')
                        rank = payload.get('rank')
                        if cid in candidate_pnl and rank is not None:
                            rank_pnl[rank].append(candidate_pnl[cid])
                            rank_counts[rank] += 1
                except:
                    pass

print("\n--- Cumulative PnL by Rank ---")
for rank in sorted(rank_pnl.keys()):
    if rank <= 10:
        pnls = rank_pnl[rank]
        total_pnl = sum(pnls)
        print(f"Rank {rank}: Trades={len(pnls)}, Cumulative PnL={total_pnl:.6f}, Avg Expectancy={total_pnl/len(pnls):.6f}")

