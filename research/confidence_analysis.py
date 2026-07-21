import pandas as pd
import numpy as np
from scipy.stats import pearsonr, spearmanr
import json

df = pd.read_csv('/app/research/trades_snapshot.csv')

# Separate buys and sells
buys = df[df['side'].str.upper() == 'BUY'].copy()
sells = df[df['side'].str.upper() == 'SELL'].copy()

# Keep confidence from buys
buys = buys[['trade_uuid', 'confidence']]

# Merge confidence into sells
completed = sells.merge(buys, on='trade_uuid', suffixes=('', '_buy'))
completed['confidence'] = completed['confidence_buy'] # override the 0.0 with actual buy confidence

for c in ['pnl', 'mfe', 'mae', 'confidence']:
    completed[c] = pd.to_numeric(completed[c], errors='coerce')
completed = completed.dropna(subset=['confidence', 'pnl'])

# Deciles
completed['decile'] = pd.qcut(completed['confidence'], q=10, duplicates='drop')

decile_stats = []
for decile, group in completed.groupby('decile', observed=False):
    n = len(group)
    if n == 0:
        continue
    
    win_rate = (group['pnl'] > 0).mean()
    avg_pnl = group['pnl'].mean()
    median_pnl = group['pnl'].median()
    avg_mfe = group['mfe'].mean()
    avg_mae = group['mae'].mean()
    
    winners = group[group['pnl'] > 0]
    losers = group[group['pnl'] <= 0]
    avg_winner = winners['pnl'].mean() if not winners.empty else 0
    avg_loser = losers['pnl'].mean() if not losers.empty else 0
    
    sum_win = winners['pnl'].sum() if not winners.empty else 0
    sum_loss = abs(losers['pnl'].sum()) if not losers.empty else 0
    profit_factor = sum_win / sum_loss if sum_loss != 0 else (np.inf if sum_win > 0 else 0)
    expectancy = (win_rate * avg_winner) - ((1 - win_rate) * abs(avg_loser))
    
    decile_stats.append({
        'range': str(decile),
        'n': n,
        'win_rate': win_rate,
        'expectancy': expectancy,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'avg_mfe': avg_mfe,
        'avg_mae': avg_mae,
        'profit_factor': profit_factor
    })

# Correlations
pearson_coef, pearson_p = pearsonr(completed['confidence'], completed['pnl'])
spearman_coef, spearman_p = spearmanr(completed['confidence'], completed['pnl'])

# Top 20% vs Bottom 20%
q20 = completed['confidence'].quantile(0.2)
q80 = completed['confidence'].quantile(0.8)

top_20 = completed[completed['confidence'] >= q80]
bot_20 = completed[completed['confidence'] <= q20]

def get_stats(group):
    n = len(group)
    winners = group[group['pnl'] > 0]
    losers = group[group['pnl'] <= 0]
    win_rate = len(winners) / n if n > 0 else 0
    sum_win = winners['pnl'].sum() if not winners.empty else 0
    sum_loss = abs(losers['pnl'].sum()) if not losers.empty else 0
    pf = sum_win / sum_loss if sum_loss != 0 else (np.inf if sum_win > 0 else 0)
    avg_winner = winners['pnl'].mean() if not winners.empty else 0
    avg_loser = losers['pnl'].mean() if not losers.empty else 0
    exp = (win_rate * avg_winner) - ((1 - win_rate) * abs(avg_loser))
    return {
        'n': n,
        'total_pnl': group['pnl'].sum() if n > 0 else 0,
        'expectancy': exp,
        'profit_factor': pf
    }

out = {
    'deciles': decile_stats,
    'correlations': {
        'pearson': {'coef': pearson_coef, 'p': pearson_p},
        'spearman': {'coef': spearman_coef, 'p': spearman_p}
    },
    'comparison': {
        'top_20': get_stats(top_20),
        'bottom_20': get_stats(bot_20)
    }
}

print(json.dumps(out, indent=2))
