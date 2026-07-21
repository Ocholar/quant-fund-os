import pandas as pd
import numpy as np

# Load data
df = pd.read_csv('/app/research/trades_snapshot.csv')

# Filter to completed trades (side = SELL)
completed = df[df['side'].str.upper() == 'SELL'].copy()

# Ensure numeric columns
numeric_cols = ['pnl', 'mfe', 'mae']
for c in numeric_cols:
    completed[c] = pd.to_numeric(completed[c], errors='coerce')

results = []

for exit_reason, group in completed.groupby('exit_reason'):
    n = len(group)
    if n == 0:
        continue
    
    pnl = group['pnl']
    winners = group[group['pnl'] > 0]
    losers = group[group['pnl'] <= 0]
    
    win_rate = len(winners) / n
    total_pnl = pnl.sum()
    avg_pnl = pnl.mean()
    median_pnl = pnl.median()
    
    avg_winner = winners['pnl'].mean() if not winners.empty else 0
    avg_loser = losers['pnl'].mean() if not losers.empty else 0
    
    sum_win = winners['pnl'].sum() if not winners.empty else 0
    sum_loss = abs(losers['pnl'].sum()) if not losers.empty else 0
    profit_factor = sum_win / sum_loss if sum_loss != 0 else (np.inf if sum_win > 0 else 0)
    
    expectancy = (win_rate * avg_winner) - ((1 - win_rate) * abs(avg_loser))
    
    avg_mfe = group['mfe'].mean()
    avg_mae = group['mae'].mean()
    
    # Exit efficiency
    # winners: realized profit / mfe
    eff_win = (winners['pnl'] / winners['mfe']).mean() if not winners.empty else 0
    # losers: realized loss (negative pnl) / mae (usually negative or positive depending on schema).
    # Assuming mae is the maximum adverse excursion (can be negative). We want absolute loss / absolute mae.
    eff_loss = (losers['pnl'] / losers['mae']).mean() if not losers.empty else 0
    
    # 95% CI for mean PnL
    std_pnl = pnl.std(ddof=1) if n > 1 else 0
    ci_margin = 1.96 * (std_pnl / np.sqrt(n)) if n > 1 else 0
    
    results.append({
        'exit_reason': exit_reason,
        'n': n,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'avg_winner': avg_winner,
        'avg_loser': avg_loser,
        'profit_factor': profit_factor,
        'expectancy': expectancy,
        'avg_mfe': avg_mfe,
        'avg_mae': avg_mae,
        'eff_win': eff_win,
        'eff_loss': eff_loss,
        'ci_margin': ci_margin
    })

# Convert to dataframe and sort by expectancy descending
res_df = pd.DataFrame(results)
res_df = res_df.sort_values(by='expectancy', ascending=False)

import json
print(res_df.to_json(orient='records', indent=2))
