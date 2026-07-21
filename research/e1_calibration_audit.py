import pandas as pd
import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import calibration_curve
from sklearn.isotonic import IsotonicRegression
import matplotlib.pyplot as plt

print("============================================================")
print("TASK E1.1 - CONFIDENCE CALIBRATION AUDIT")
print("============================================================")

df = pd.read_csv('research/feature_dataset.csv')
df['winner'] = df['pnl'] > 0

# Step 1: Compute raw_score
raw_score = (
    (-4.37 * df['one_tick_momentum']) +
    ( 2.39 * df['long_trend']) +
    ( 1.52 * df['trend']) +
    (-0.49 * df['volatility_log']) +
    (-0.23 * df['breakout_score']) +
    ( 0.11 * df['trend_quality'])
)
df['raw_score'] = raw_score

export_cols = ['trade_uuid', 'symbol', 'raw_score', 'pnl', 'winner']
df[export_cols].to_csv('research/raw_score_export.csv', index=False)

# Step 2: raw_score statistics
print("\n--- Step 2: Raw Score Statistics ---")
print(f"Mean:   {df['raw_score'].mean():.6f}")
print(f"Std:    {df['raw_score'].std():.6f}")
print(f"Min:    {df['raw_score'].min():.6f}")
print(f"Max:    {df['raw_score'].max():.6f}")
print("Percentiles:")
print(df['raw_score'].quantile([0.05, 0.25, 0.50, 0.75, 0.95]).to_string())

hist, bins = np.histogram(df['raw_score'], bins=10)
print("\nHistogram:")
for i in range(10):
    print(f"[{bins[i]:.4f} - {bins[i+1]:.4f}]: {hist[i]}")

# Step 3: Compute Predictiveness
print("\n--- Step 3: Predictiveness ---")
pearson = df['raw_score'].corr(df['pnl'], method='pearson')
spearman = df['raw_score'].corr(df['pnl'], method='spearman')
roc_auc = roc_auc_score(df['winner'], df['raw_score'])

print(f"Pearson(raw_score, pnl):  {pearson:.4f}")
print(f"Spearman(raw_score, pnl): {spearman:.4f}")
print(f"ROC AUC:                  {roc_auc:.4f}")

# Step 4: Determine outcome
print("\n--- Step 4: Outcome Determination ---")
if roc_auc > 0.55:
    print("Outcome A: raw_score is predictive. Proceeding to Calibration (Platt Scaling).")
    
    # Step 5: Platt Scaling (Logistic Regression)
    # Fit logistic regression on raw_score to predict winner
    X = df[['raw_score']]
    y = df['winner']
    
    lr = LogisticRegression(fit_intercept=True)
    lr.fit(X, y)
    
    print(f"\nPlatt Scaling Coefficients: Intercept = {lr.intercept_[0]:.4f}, Coef = {lr.coef_[0][0]:.4f}")
    
    df['calibrated_confidence'] = lr.predict_proba(X)[:, 1]
    
    # Let's also try Isotonic Regression to see if it works better
    iso = IsotonicRegression(out_of_bounds='clip')
    df['iso_confidence'] = iso.fit_transform(df['raw_score'], df['winner'])

    # Step 6: Replay again using calibrated confidence
    print("\n--- Step 6: Calibrated Replay (Platt Scaling) ---")
    df['accepted_calibrated'] = df['calibrated_confidence'] > 0.80
    
    # If 0.8 is too high, maybe check the max calibrated confidence
    max_cal_conf = df['calibrated_confidence'].max()
    print(f"Max calibrated confidence achievable: {max_cal_conf:.4f}")
    
    if max_cal_conf <= 0.80:
        print("\nWARNING: Even calibrated, no trades reach > 0.80 confidence.")
        print("Using > 0.55 threshold just to see separation...")
        df['accepted_calibrated'] = df['calibrated_confidence'] > 0.55

    kept_df = df[df['accepted_calibrated']]
    filtered_df = df[~df['accepted_calibrated']]
    
    print(f"Trades retained: {len(kept_df)}")
    print(f"Trades filtered: {len(filtered_df)}")
    
    if len(kept_df) > 0:
        win_rate = kept_df['winner'].mean()
        expectancy = kept_df['pnl'].mean()
        winners = kept_df[kept_df['winner']]
        losers = kept_df[~kept_df['winner']]
        gross_profit = winners['pnl'].sum()
        gross_loss = abs(losers['pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
        
        print(f"Win rate: {win_rate:.4f}")
        print(f"Expectancy: {expectancy:.6f}")
        print(f"Profit factor: {profit_factor:.4f}")
        
        print(f"\nDecision Matrix:")
        print(f"Kept Winner:     {len(winners)}")
        print(f"Kept Loser:      {len(losers)}")
        print(f"Filtered Winner: {len(filtered_df[filtered_df['winner']])}")
        print(f"Filtered Loser:  {len(filtered_df[~filtered_df['winner']])}")
    else:
        print("No trades retained. Calibration cannot separate winners enough to pass the threshold.")
elif roc_auc < 0.45:
    print("Outcome C: raw_score is anti-predictive. Feature signs are wrong.")
else:
    print("Outcome B: raw_score is random. Feature weighting is wrong, or signal is too weak.")

