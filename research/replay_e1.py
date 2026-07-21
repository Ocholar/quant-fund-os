import pandas as pd
import numpy as np

print("============================================================")
print("EXPERIMENT E1 OFFLINE REPLAY")
print("============================================================")

# Load dataset
df = pd.read_csv('research/feature_dataset.csv')
print(f"Loaded {len(df)} historical trades.")

# Old metrics are already in the dataset
df['confidence_old'] = df['confidence']
df['accepted_old'] = df['confidence_old'] > 0.80

# New logic
raw_score = (
    (-4.37 * df['one_tick_momentum']) +
    ( 2.39 * df['long_trend']) +
    ( 1.52 * df['trend']) +
    (-0.49 * df['volatility_log']) +
    (-0.23 * df['breakout_score']) +
    ( 0.11 * df['trend_quality'])
)
df['raw_score'] = raw_score
df['confidence_new'] = 1.0 / (1.0 + np.exp(-df['raw_score']))
df['accepted_new'] = df['confidence_new'] > 0.80

# Basic metrics
print(f"\nOld accepted: {df['accepted_old'].sum()}")
print(f"New accepted: {df['accepted_new'].sum()}")
print(f"Trades filtered: {len(df) - df['accepted_new'].sum()}")
print(f"Trades kept: {df['accepted_new'].sum()}")

# PnL metrics
filtered_df = df[~df['accepted_new']]
kept_df = df[df['accepted_new']]

print(f"\nFilter Quality:")
print(f"PnL of filtered trades: {filtered_df['pnl'].sum():.6f}")
print(f"PnL of retained trades: {kept_df['pnl'].sum():.6f}")

print(f"\nRetained Trades Performance:")
print(f"Average expectancy: {kept_df['pnl'].mean():.6f}")
if len(kept_df) > 0:
    winners = kept_df[kept_df['pnl'] > 0]
    losers = kept_df[kept_df['pnl'] < 0]
    win_rate = len(winners) / len(kept_df) if len(kept_df) > 0 else 0
    gross_profit = winners['pnl'].sum()
    gross_loss = abs(losers['pnl'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
    avg_mae = kept_df['mae'].mean() if 'mae' in kept_df.columns else 0.0
    avg_mfe = kept_df['mfe'].mean() if 'mfe' in kept_df.columns else 0.0
    
    print(f"Profit factor: {profit_factor:.4f}")
    print(f"Win rate: {win_rate:.4f}")
    print(f"Average MAE: {avg_mae:.6f}")
    print(f"Average MFE: {avg_mfe:.6f}")
else:
    print("No trades retained.")

# Decision Matrix
df['winner'] = df['pnl'] > 0
kept_winners = df[df['accepted_new'] & df['winner']]
kept_losers = df[df['accepted_new'] & ~df['winner']]
filtered_winners = df[~df['accepted_new'] & df['winner']]
filtered_losers = df[~df['accepted_new'] & ~df['winner']]

print(f"\nDecision Matrix:")
print(f"Kept Winner:     {len(kept_winners)}")
print(f"Kept Loser:      {len(kept_losers)}")
print(f"Filtered Winner: {len(filtered_winners)}")
print(f"Filtered Loser:  {len(filtered_losers)}")

# Symbol Impact
print(f"\nSymbol Impact:")
symbol_counts_all = df['symbol'].value_counts()
symbol_counts_kept = kept_df['symbol'].value_counts()
symbol_counts_filtered = filtered_df['symbol'].value_counts()

print("Top symbols removed:")
print(symbol_counts_filtered.head(5).to_string())
print("\nTop symbols retained:")
print(symbol_counts_kept.head(5).to_string())

print("\nSpecifically verify (BILL, TRIA, EDEN):")
for sym in ['BILL/USDT', 'TRIA/USDT', 'EDEN/USDT']:
    total = symbol_counts_all.get(sym, 0)
    kept = symbol_counts_kept.get(sym, 0)
    filtered = symbol_counts_filtered.get(sym, 0)
    print(f"  {sym}: Total={total}, Kept={kept}, Filtered={filtered}")

# Exit Distribution
if 'exit_strategy' in df.columns:
    print("\nExit Distribution (Old vs New):")
    old_exits = df['exit_strategy'].value_counts()
    new_exits = kept_df['exit_strategy'].value_counts()
    exit_df = pd.DataFrame({'Old': old_exits, 'New': new_exits}).fillna(0).astype(int)
    print(exit_df.to_string())

# Confidence Distribution
print("\nConfidence Distribution (Old vs New):")
old_hist, old_bins = np.histogram(df['confidence_old'], bins=10, range=(0, 1))
new_hist, new_bins = np.histogram(df['confidence_new'], bins=10, range=(0, 1))
hist_df = pd.DataFrame({
    'Range': [f"{old_bins[i]:.1f}-{old_bins[i+1]:.1f}" for i in range(10)],
    'Old Count': old_hist,
    'New Count': new_hist
})
print(hist_df.to_string(index=False))
