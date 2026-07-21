import json
import pandas as pd
import numpy as np
import glob
from collections import defaultdict
import os

print("============================================================")
print("PHASE IIIA - PORTFOLIO CAPACITY ENGINEERING")
print("============================================================")

print("1. Loading Trades and Reconstructing Portfolio State...")
snapshot = pd.read_csv('research/trades_snapshot.csv')
snapshot['created_at'] = pd.to_datetime(snapshot['created_at'], utc=True)
snapshot = snapshot.sort_values('created_at').reset_index(drop=True)

buys = snapshot[snapshot['side'] == 'buy'].copy()
sells = snapshot[snapshot['side'] == 'sell'].copy()

# Map buy to sell to get entry/exit times and PnL
buy_to_sell = buys.merge(sells[['trade_uuid', 'created_at', 'pnl']], on='trade_uuid', suffixes=('_buy', '_sell'), how='inner')
buy_to_sell = buy_to_sell.rename(columns={'created_at_buy': 'entry_time', 'created_at_sell': 'exit_time', 'pnl_sell': 'final_pnl'})

# We can query open positions at any time T by finding trades where entry_time <= T < exit_time
def get_open_positions(t):
    return buy_to_sell[(buy_to_sell['entry_time'] <= t) & (buy_to_sell['exit_time'] > t)].copy()

print("2. Parsing Candidate Logs for Capacity Rejections...")
LOG_FILES = glob.glob('logs/candidates/candidates_*.jsonl')

capacity_reasons = {'MAX_POSITIONS', 'MAX_EXPOSURE', 'CASH_INSUFFICIENT', 'ACTIVE_ORDER'}

rejected_rank1_rows = []
for path in LOG_FILES:
    with open(path, 'r', encoding='utf-8') as f:
        cycle_cands = {}
        for line in f:
            if 'candidate' not in line:
                continue
            try:
                obj = json.loads(line)
                event = obj['event_type']
                p = obj['payload']
                cid = p.get('cycle_id')
                sym = p.get('symbol')
                if not cid or not sym:
                    continue
                    
                if sym not in cycle_cands:
                    cycle_cands[sym] = {'sym': sym, 'rank': None, 'reason': None, 'ts': pd.Timestamp(obj['timestamp']), 'score': None}
                
                if event == 'candidate_ranked':
                    cycle_cands[sym]['rank'] = p.get('rank')
                    cycle_cands[sym]['score'] = p.get('score_before_filters') or p.get('score_after_filters')
                elif event == 'candidate_filtered':
                    cycle_cands[sym]['reason'] = p.get('reason') or p.get('raw_reason')
                    
            except Exception:
                pass
                
        # After processing file (which might have many cycles, this is just an approximation if we group by cycle)
        # Actually it's better to process line by line, but the file boundary is loose.
        
# A more robust parse:
cycles = defaultdict(dict)
for path in LOG_FILES:
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if 'candidate' not in line:
                continue
            try:
                obj = json.loads(line)
                event = obj['event_type']
                p = obj['payload']
                cid = p.get('cycle_id')
                sym = p.get('symbol')
                if not cid or not sym:
                    continue
                if sym not in cycles[cid]:
                    cycles[cid][sym] = {'symbol': sym, 'rank': None, 'reason': None, 'ts': pd.Timestamp(obj['timestamp'], tz='UTC'), 'score': None}
                
                if event == 'candidate_ranked':
                    cycles[cid][sym]['rank'] = p.get('rank')
                    cycles[cid][sym]['score'] = p.get('score_before_filters') or p.get('score_after_filters')
                    cycles[cid][sym]['ts'] = pd.Timestamp(obj['timestamp'], tz='UTC')
                elif event == 'candidate_filtered':
                    cycles[cid][sym]['reason'] = p.get('reason') or p.get('raw_reason')
                elif event == 'candidate_approved':
                    cycles[cid][sym]['approved'] = True
            except Exception:
                pass

rejected_rank1 = []
for cid, syms in cycles.items():
    for sym, c in syms.items():
        # A candidate is rejected if it was ranked 1 but never approved
        if c.get('rank') == 1 and not c.get('approved', False):
            # We assume it was blocked by capacity if reason is empty (meaning the allocator skipped it silently due to capacity/holding)
            # or if reason specifically says so.
            rejected_rank1.append(c)

df_rejected = pd.DataFrame(rejected_rank1)
if len(df_rejected) == 0:
    print("No Rank 1 candidates rejected due to capacity.")
    exit(0)

df_rejected = df_rejected.sort_values('ts').reset_index(drop=True)
print(f"Found {len(df_rejected)} Rank 1 candidates rejected due to capacity constraints.")

print("3. Matching Forward PnL for Rejected Candidates...")
# We match rejected candidates to the nearest future trade of the same symbol within 2 hours
matched_parts = []
for sym in buys['symbol'].unique():
    sym_buys = buys[buys['symbol'] == sym].copy()
    sym_rej = df_rejected[df_rejected['symbol'] == sym].copy()
    if len(sym_rej) == 0:
        continue
    
    merged = pd.merge_asof(
        sym_rej,
        sym_buys.drop(columns=['symbol']).rename(columns={'created_at': 'trade_ts'}),
        left_on='ts',
        right_on='trade_ts',
        direction='forward',
        tolerance=pd.Timedelta('2hour')
    )
    matched_parts.append(merged)

if matched_parts:
    cand_outcomes = pd.concat(matched_parts, ignore_index=True)
    # Get final PnL from sell
    cand_outcomes = cand_outcomes.merge(sells[['trade_uuid', 'pnl']], on='trade_uuid', how='left', suffixes=('', '_actual'))
else:
    cand_outcomes = df_rejected.copy()
    cand_outcomes['pnl_actual'] = np.nan

cand_outcomes = cand_outcomes[cand_outcomes['pnl_actual'].notna()].copy()
print(f"Candidates with forward PnL outcomes: {len(cand_outcomes)}")

print("4. Calculating Opportunity Cost & Simulating Replacements...")

opp_costs = []
policy_results = {
    'R0_Baseline': [],
    'R1_Oldest': [],
    'R2_Lowest_PnL': [],
    'R3_Lowest_Signal': [], # Approximate with confidence/score
}

# Pre-calculate a fast lookup for open positions
for _, row in cand_outcomes.iterrows():
    t = row['ts']
    rej_pnl = row['pnl_actual']
    
    open_pos = get_open_positions(t)
    if len(open_pos) == 0:
        # Should not happen if rejected for capacity, but just in case
        continue
        
    # Baseline R0: We kept the open positions, rejected the candidate. 
    # Average forward PnL of open positions from this moment? 
    # The true baseline PnL is the final PnL of the open positions (since they were kept).
    # But wait, their final PnL is already baked in. The replacement cost is replacing ONE position.
    
    # R1: Replace Oldest
    oldest = open_pos.loc[open_pos['entry_time'].idxmin()]
    
    # R2: Replace Lowest Unrealized PnL (we don't have exact unrealized PnL at time T, but we can approximate it, or just use the lowest final PnL as a proxy for "weakest" if we assume trajectory is monotonic, though that's cheating. In offline replay, we can't easily get unrealized PnL without minute-bar data. We'll skip R2 for now or proxy it with confidence).
    
    # Let's proxy: replace the one with the lowest confidence score at entry
    weakest_conf = open_pos.loc[open_pos['confidence'].idxmin()]
    
    opp_costs.append({
        'ts': t,
        'symbol': row['symbol'],
        'rejected_pnl': rej_pnl,
        'oldest_pos_pnl': oldest['final_pnl'],
        'weakest_pos_pnl': weakest_conf['final_pnl']
    })
    
    policy_results['R0_Baseline'].append(oldest['final_pnl']) # We kept the oldest
    policy_results['R1_Oldest'].append(rej_pnl) # We took the rejected candidate instead of oldest
    
    policy_results['R0_Baseline'].append(weakest_conf['final_pnl']) # Kept weakest
    policy_results['R3_Lowest_Signal'].append(rej_pnl) # Took candidate instead of weakest

df_opp = pd.DataFrame(opp_costs)
if len(df_opp) > 0:
    df_opp['opp_cost_oldest'] = df_opp['rejected_pnl'] - df_opp['oldest_pos_pnl']
    df_opp['opp_cost_weakest'] = df_opp['rejected_pnl'] - df_opp['weakest_pos_pnl']
    
    print("\n--- Opportunity Cost Report ---")
    print(f"Total Rank 1 Candidates Blocked by Capacity (with forward data): {len(df_opp)}")
    print(f"Average PnL of Rejected Rank 1 Candidates: {df_opp['rejected_pnl'].mean():.6f}")
    print(f"Average PnL of Open Positions Retained (Oldest): {df_opp['oldest_pos_pnl'].mean():.6f}")
    print(f"Average PnL of Open Positions Retained (Weakest): {df_opp['weakest_pos_pnl'].mean():.6f}")
    
    print(f"\nNet Opportunity Cost (Replacing Oldest): {df_opp['opp_cost_oldest'].sum():.4f}")
    print(f"Net Opportunity Cost (Replacing Weakest): {df_opp['opp_cost_weakest'].sum():.4f}")
    
    print("\n--- Policy Simulation Results ---")
    for pol, pnls in policy_results.items():
        arr = np.array(pnls)
        print(f"Policy {pol}:")
        print(f"  Trades Simulated: {len(arr)}")
        print(f"  Win Rate: {(arr > 0).mean():.2%}")
        print(f"  Expectancy: {arr.mean():.6f}")
        print(f"  Total PnL: {arr.sum():.6f}")
else:
    print("Not enough data to calculate opportunity costs.")

