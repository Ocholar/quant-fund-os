import pandas as pd
import numpy as np
import json

df = pd.read_csv('/app/research/trades_snapshot.csv')

# We only want completed trades. 
completed = df[df['side'].str.upper() == 'SELL'].copy()
for c in ['pnl', 'mfe', 'mae']:
    completed[c] = pd.to_numeric(completed[c], errors='coerce')
completed = completed.dropna(subset=['pnl'])

results = []
for sym, group in completed.groupby('symbol'):
    n = len(group)
    if n == 0:
        continue
    
    win_rate = (group['pnl'] > 0).mean()
    total_pnl = group['pnl'].sum()
    avg_pnl = group['pnl'].mean()
    median_pnl = group['pnl'].median()
    
    winners = group[group['pnl'] > 0]
    losers = group[group['pnl'] <= 0]
    
    avg_winner = winners['pnl'].mean() if not winners.empty else 0
    avg_loser = losers['pnl'].mean() if not losers.empty else 0
    
    sum_win = winners['pnl'].sum() if not winners.empty else 0
    sum_loss = abs(losers['pnl'].sum()) if not losers.empty else 0
    profit_factor = sum_win / sum_loss if sum_loss != 0 else (np.inf if sum_win > 0 else 0)
    
    expectancy = (win_rate * avg_winner) - ((1 - win_rate) * abs(avg_loser))
    
    std_pnl = group['pnl'].std(ddof=1) if n > 1 else 0
    ci_margin = 1.96 * (std_pnl / np.sqrt(n)) if n > 1 else 0
    
    results.append({
        'symbol': sym,
        'n': n,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'avg_winner': avg_winner,
        'avg_loser': avg_loser,
        'profit_factor': profit_factor,
        'expectancy': expectancy,
        'ci_margin': ci_margin
    })

res_df = pd.DataFrame(results)

# Top 10 by expectancy
top10_exp = res_df.sort_values(by='expectancy', ascending=False).head(10).to_dict('records')
bot10_exp = res_df.sort_values(by='expectancy', ascending=True).head(10).to_dict('records')

top10_pnl = res_df.sort_values(by='total_pnl', ascending=False).head(10).to_dict('records')
bot10_pnl = res_df.sort_values(by='total_pnl', ascending=True).head(10).to_dict('records')

out = {
    'all_symbols': res_df.to_dict('records'),
    'top10_exp': top10_exp,
    'bot10_exp': bot10_exp,
    'top10_pnl': top10_pnl,
    'bot10_pnl': bot10_pnl
}

print(json.dumps(out))
