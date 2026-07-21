import json
import pandas as pd
import numpy as np
import os
import glob

print("============================================================")
print("TASK E1.2 - CANDIDATE SELECTION AUDIT")
print("============================================================")

LOG_FILES = glob.glob('logs/candidates/candidates_*.jsonl')

print("1. Parsing candidate logs to extract rank...")
ranked_rows = []
for path in LOG_FILES:
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if 'candidate_ranked' not in line:
                continue
            try:
                obj = json.loads(line)
                if obj['event_type'] != 'candidate_ranked':
                    continue
                p = obj['payload']
                if p.get('rank') is None:
                    continue
                ranked_rows.append({
                    'symbol': p['symbol'],
                    'ts': pd.Timestamp(obj['timestamp']),
                    'rank': int(p['rank']),
                    'candidate_id': p.get('candidate_id')
                })
            except Exception:
                pass

ranked = pd.DataFrame(ranked_rows)
ranked['ts'] = ranked['ts'].dt.tz_convert('UTC')
ranked = ranked.sort_values('ts').reset_index(drop=True)
print(f"Loaded {len(ranked):,} ranked candidate events.")

print("2. Loading completed trades...")
# trades_snapshot.csv has buy and sell trades. 
# We can load feature_dataset.csv which is already just the BUY trades with joined PnL from the SELL.
trades = pd.read_csv('research/feature_dataset.csv')
trades['ts'] = pd.to_datetime(trades['trade_uuid'].apply(lambda x: x if len(str(x))<30 else None), errors='ignore') 
# wait feature_dataset has no ts column! But we can just use the db again or we can re-do the merge.
# It's better to load trades_snapshot.csv directly.
snapshot = pd.read_csv('research/trades_snapshot.csv')
snapshot['created_at'] = pd.to_datetime(snapshot['created_at'], utc=True)
buys = snapshot[snapshot['side'] == 'buy'].drop(columns=['pnl', 'mfe', 'mae', 'exit_reason'], errors='ignore').copy().sort_values('created_at').reset_index(drop=True)
sells = snapshot[snapshot['side'] == 'sell'].copy()

print("3. Vectorized merge_asof per symbol...")
matched_parts = []
for sym in buys['symbol'].unique():
    sym_buys = buys[buys['symbol'] == sym].copy()
    sym_ranked = ranked[ranked['symbol'] == sym].copy()
    if len(sym_ranked) == 0:
        continue

    merged = pd.merge_asof(
        sym_buys.rename(columns={'created_at': 'ts'}),
        sym_ranked[['ts', 'rank', 'candidate_id']],
        on='ts',
        direction='nearest',
        tolerance=pd.Timedelta('5min'),
    )
    merged = merged.rename(columns={'ts': 'created_at'})
    matched_parts.append(merged)

buy_feat = pd.concat(matched_parts, ignore_index=True)

print("4. Joining to SELL outcomes to get PnL...")
sell_out = sells[['trade_uuid', 'pnl']].copy()
completed = buy_feat.merge(sell_out, on='trade_uuid', how='inner')
print(f"Matched {len(completed)} trades with rank and PnL.")

print("\n--- Cumulative PnL by Rank ---")
results = []
for rank in sorted(completed['rank'].dropna().unique()):
    if rank <= 5:
        rank_trades = completed[completed['rank'] == rank]
        pnls = rank_trades['pnl']
        total_pnl = pnls.sum()
        avg_pnl = pnls.mean()
        win_rate = (pnls > 0).mean()
        results.append({
            'Rank': rank,
            'Trades': len(rank_trades),
            'Win Rate': f"{win_rate:.2%}",
            'Cumulative PnL': f"{total_pnl:.6f}",
            'Avg PnL': f"{avg_pnl:.6f}"
        })

results_df = pd.DataFrame(results)
print(results_df.to_string(index=False))

print("\nAnalysis: If Rank 2 or 3 has a higher average PnL than Rank 1, the allocator is negatively sorting candidates.")
