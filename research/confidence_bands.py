import pandas as pd
import numpy as np

df = pd.read_csv('C:/Users/Administrator/Documents/quant-fund-os/research/trades_snapshot.csv')
buys = df[df['side']=='buy'].copy()
sells = df[df['side']=='sell'].copy()

# Map buy confidence to sells via trade_uuid
buys_map = buys[['trade_uuid','confidence']].rename(columns={'confidence':'buy_confidence'})
completed = sells.merge(buys_map, on='trade_uuid', how='inner')

# Split by confidence band
low = completed[completed['buy_confidence'] < 0.80]
mid = completed[(completed['buy_confidence'] >= 0.80) & (completed['buy_confidence'] < 0.88)]
hi = completed[completed['buy_confidence'] >= 0.88]

for label, grp in [('LOW <0.80', low), ('MID 0.80-0.88', mid), ('HIGH >=0.88', hi)]:
    n = len(grp)
    if n == 0:
        continue
    wr = (grp['pnl'] > 0).mean()
    avg_pnl = grp['pnl'].mean()
    avg_mfe = grp['mfe'].mean()
    avg_mae = grp['mae'].mean()
    stop_pct = (grp['exit_reason'] == 'sideways_stop_loss_exit').sum() / n * 100
    print(f"{label}: n={n} winrate={wr:.3f} avgPnL={avg_pnl:.6f} avgMFE={avg_mfe:.6f} avgMAE={avg_mae:.6f} stopLoss%={stop_pct:.1f}%")

print()
print("=== Correlation: buy_confidence vs outcome ===")
from scipy.stats import spearmanr
coef, pval = spearmanr(completed['buy_confidence'], completed['pnl'])
print(f"Spearman(buy_confidence, pnl): r={coef:.4f} p={pval:.4f}")

# What about signal_strength from the feature store? Check if any raw feature data exists.
print()
print("=== Feature-level columns in dataset ===")
for col in df.columns:
    print(f"  {col}")
