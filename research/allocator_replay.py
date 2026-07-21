import json
import pandas as pd
import glob
from collections import defaultdict
import numpy as np

print("============================================================")
print("TASK A1 - FULL ALLOCATOR DECISION REPLAY")
print("============================================================")

LOG_FILES = glob.glob('logs/candidates/candidates_*.jsonl')

# 1. Parse candidate logs to reconstruct each cycle
cycles = defaultdict(dict)  # cycle_id -> { candidates: { symbol -> { rank, score, features, filtered_reason, approved } } }

print("1. Parsing candidate logs...")
for path in LOG_FILES:
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if 'candidate' not in line:
                continue
            try:
                obj = json.loads(line)
                event = obj['event_type']
                p = obj['payload']
                cycle_id = p.get('cycle_id')
                if not cycle_id:
                    continue
                
                sym = p.get('symbol')
                if sym not in cycles[cycle_id]:
                    cycles[cycle_id][sym] = {
                        'symbol': sym,
                        'rank': None,
                        'score': None,
                        'features': None,
                        'filtered_reason': None,
                        'approved': False,
                        'ts': pd.Timestamp(obj['timestamp'])
                    }
                
                cand = cycles[cycle_id][sym]
                
                if event == 'candidate_ranked':
                    cand['rank'] = p.get('rank')
                    cand['score'] = p.get('score_before_filters') or p.get('score_after_filters')
                    cand['features'] = p.get('features')
                    cand['ts'] = pd.Timestamp(obj['timestamp'])
                elif event == 'candidate_filtered':
                    cand['filtered_reason'] = p.get('reason') or p.get('raw_reason')
                elif event == 'candidate_approved':
                    cand['approved'] = True
                    
            except Exception:
                pass

print(f"Loaded {len(cycles)} trading cycles.")

# Build a list of ranked candidates
all_candidates = []
for cid, syms in cycles.items():
    for sym, cand in syms.items():
        if cand['rank'] is not None:
            cand['cycle_id'] = cid
            all_candidates.append(cand)

candidates_df = pd.DataFrame(all_candidates)
if len(candidates_df) == 0:
    print("No ranked candidates found.")
    exit(0)

candidates_df['ts'] = candidates_df['ts'].dt.tz_convert('UTC')
candidates_df = candidates_df.sort_values('ts').reset_index(drop=True)

# 2. Match to trades_snapshot to get PnL for ALL trades (so we know future outcomes)
print("2. Mapping candidates to future PnL...")
snapshot = pd.read_csv('research/trades_snapshot.csv')
snapshot['created_at'] = pd.to_datetime(snapshot['created_at'], utc=True)
buys = snapshot[snapshot['side'] == 'buy'].drop(columns=['pnl', 'mfe', 'mae', 'exit_reason'], errors='ignore').copy().sort_values('created_at').reset_index(drop=True)
sells = snapshot[snapshot['side'] == 'sell'].copy()
sell_out = sells[['trade_uuid', 'pnl', 'mfe', 'mae']].copy()

# For candidates that were NOT approved in this cycle, we still want to know their forward PnL.
# To approximate "what if it was taken", we can find the nearest future BUY for this symbol, OR
# since trades_snapshot only contains actual trades, we can only simulate using actual trades.
# Wait, if Rank 1 was skipped, and we want to know "What if Rank 1 was taken?", we can only know its outcome IF it was eventually traded nearby, OR if we had forward price data.
# Since we don't have price data, we can only evaluate predictive power on candidates that EVENTUALLY resulted in a trade, 
# OR we evaluate the ranks of candidates that WERE traded.
# Let's match every candidate to the NEAREST future trade within 1 hour.
print("Matching candidates to nearest future trade within 1 hour to approximate outcome...")

matched_parts = []
for sym in buys['symbol'].unique():
    sym_buys = buys[buys['symbol'] == sym].copy()
    sym_cands = candidates_df[candidates_df['symbol'] == sym].copy()
    if len(sym_cands) == 0:
        continue
    
    # We want the FIRST trade that happens AFTER or EXACTLY AT the candidate's timestamp.
    # direction='forward' matches the closest ts in sym_buys >= ts in sym_cands
    merged = pd.merge_asof(
        sym_cands,
        sym_buys.rename(columns={'created_at': 'trade_ts'}),
        left_on='ts',
        right_on='trade_ts',
        direction='forward',
        tolerance=pd.Timedelta('1hour')
    )
    matched_parts.append(merged)

if matched_parts:
    cand_with_outcomes = pd.concat(matched_parts, ignore_index=True)
    cand_with_outcomes = cand_with_outcomes.merge(sell_out, on='trade_uuid', how='left')
else:
    cand_with_outcomes = candidates_df.copy()
    cand_with_outcomes['pnl'] = np.nan

print(f"Candidates with forward PnL outcomes: {cand_with_outcomes['pnl'].notna().sum()} / {len(cand_with_outcomes)}")

# 3. Simulate Policies
print("3. Simulating Policies...")

# Policy A: Actual Allocator (approved = True)
policy_a = cand_with_outcomes[cand_with_outcomes['approved'] == True].copy()

# Policy B: Always choose highest score (Rank 1) that is TRADABLE (filtered_reason is None or approved)
# Wait, if a candidate was filtered, it's NOT tradable.
# Tradable means `filtered_reason` is None OR `filtered_reason` is just things like 'MAX_POSITIONS' which means we COULD have taken it if we prioritized it.
# Actually, if it's Rank 1, it wouldn't be filtered by MAX_POSITIONS unless the portfolio is full.
# Let's define "Tradable" = not filtered by symbol-specific gating (like QUARANTINE, COOLDOWN, FEATURE_NOT_READY).
# If it's filtered by MAX_POSITIONS or MAX_EXPOSURE, it means it's structurally tradable, just blocked by portfolio capacity.
def is_tradable(row):
    r = row['filtered_reason']
    if pd.isna(r) or r is None:
        return True
    if r in ['MAX_POSITIONS', 'MAX_EXPOSURE', 'CASH_INSUFFICIENT', 'ACTIVE_ORDER', 'POSITION_ALREADY_OPEN']:
        return True
    return False

cand_with_outcomes['is_tradable'] = cand_with_outcomes.apply(is_tradable, axis=1)

# Now, group by cycle_id. 
# Policy B: Top 1 tradable candidate per cycle
# Policy C: Top 2nd tradable candidate per cycle
# Policy E: Oracle (tradable candidate with highest PnL)

results = {
    'A_Actual': [],
    'B_Rank1': [],
    'C_Rank2': [],
    'E_Oracle': []
}

for cycle_id, group in cand_with_outcomes.groupby('cycle_id'):
    tradable = group[group['is_tradable']].sort_values('rank')
    
    # A
    actual = group[group['approved'] == True]
    if len(actual) > 0 and pd.notna(actual.iloc[0]['pnl']):
        results['A_Actual'].append(actual.iloc[0]['pnl'])
        
    if len(tradable) > 0:
        # B
        rank1 = tradable.iloc[0]
        if pd.notna(rank1['pnl']):
            results['B_Rank1'].append(rank1['pnl'])
            
        # C
        if len(tradable) > 1:
            rank2 = tradable.iloc[1]
            if pd.notna(rank2['pnl']):
                results['C_Rank2'].append(rank2['pnl'])
                
        # E
        with_pnl = tradable[tradable['pnl'].notna()]
        if len(with_pnl) > 0:
            best = with_pnl.loc[with_pnl['pnl'].idxmax()]
            results['E_Oracle'].append(best['pnl'])

print("\n--- Policy Simulation Results ---")
for policy, pnls in results.items():
    if not pnls:
        continue
    pnls_arr = np.array(pnls)
    trades = len(pnls_arr)
    win_rate = (pnls_arr > 0).mean()
    total_pnl = pnls_arr.sum()
    expectancy = pnls_arr.mean()
    
    winners = pnls_arr[pnls_arr > 0]
    losers = pnls_arr[pnls_arr < 0]
    pf = winners.sum() / abs(losers.sum()) if len(losers) > 0 and losers.sum() != 0 else float('inf')
    
    print(f"Policy {policy}:")
    print(f"  Trades:     {trades}")
    print(f"  Win Rate:   {win_rate:.2%}")
    print(f"  Total PnL:  {total_pnl:.6f}")
    print(f"  Expectancy: {expectancy:.6f}")
    print(f"  Profit Fac: {pf:.4f}")

# 4. Investigate Evo Strategies
print("\n--- Auditing Evolutionary Engine Strategies ---")
# Look at the strategy scores in cand_with_outcomes
# Strategy name is logged in feature dataset, but candidate logs don't have strategy name?
# The feature dataset has 'strategy' which is like 'evo_1234'.
if 'strategy' in buys.columns:
    cand_strat = cand_with_outcomes.merge(buys[['trade_uuid', 'strategy']], on='trade_uuid', how='inner')
    if 'strategy' in cand_strat.columns:
        # Group by strategy to see if high-score strategies are better
        strat_perf = cand_strat.groupby('strategy').agg(
            trades=('pnl', 'count'),
            win_rate=('pnl', lambda x: (x > 0).mean()),
            expectancy=('pnl', 'mean'),
            avg_score=('score', 'mean')
        ).reset_index()
        
        print("Top 5 strategies by Expectancy (min 3 trades):")
        valid_strats = strat_perf[strat_perf['trades'] >= 3].sort_values('expectancy', ascending=False)
        print(valid_strats.head().to_string(index=False))
        
        print("\nBottom 5 strategies by Expectancy:")
        print(valid_strats.tail().to_string(index=False))

        print("\nCorrelation between Strategy Score and Expectancy:")
        corr = valid_strats['avg_score'].corr(valid_strats['expectancy'])
        print(f"Pearson: {corr:.4f}")
        
        if corr < 0:
            print("WARNING: The evolutionary engine is scoring losing strategies HIGHER than winning strategies.")
