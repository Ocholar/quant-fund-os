"""
Feature Attribution Study — Full Analysis
Requires /app/feature_dataset.csv from extract_features.py
"""
import pandas as pd
import numpy as np
from scipy.stats import pearsonr, spearmanr
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.metrics import roc_auc_score
import warnings
warnings.filterwarnings('ignore')

FEATURES = [
    'trend', 'long_trend', 'one_tick_momentum',
    'breakout_score', 'trend_quality', 'volatility_log', 'confidence'
]

print("=" * 60)
print("FEATURE ATTRIBUTION STUDY")
print("=" * 60)

df = pd.read_csv('research/feature_dataset.csv')
print(f"\nDataset: {len(df)} completed trades")
print(f"Win rate: {(df['pnl'] > 0).mean():.3f}")
print(f"Avg PnL: {df['pnl'].mean():.6f}")

# Drop any rows missing features
df = df.dropna(subset=FEATURES + ['pnl'])
print(f"After dropping missing: {len(df)} rows")

df['winner'] = (df['pnl'] > 0).astype(int)

X = df[FEATURES]
y_pnl = df['pnl']
y_win = df['winner']

# ── SECTION 1: Univariate Correlations ───────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 1 — UNIVARIATE CORRELATIONS vs PnL")
print("=" * 60)
print(f"{'Feature':<22} {'Pearson':>8} {'p':>8} {'Spearman':>10} {'p':>8}")
print("-" * 60)

results = []
for feat in FEATURES:
    pc, pp = pearsonr(X[feat], y_pnl)
    sc, sp = spearmanr(X[feat], y_pnl)
    results.append({'feature': feat, 'pearson': pc, 'pearson_p': pp,
                    'spearman': sc, 'spearman_p': sp})
    print(f"  {feat:<20} {pc:>+8.4f} {pp:>8.4f} {sc:>+10.4f} {sp:>8.4f}")

results_df = pd.DataFrame(results).sort_values('spearman', ascending=False)

# ── SECTION 2: Mutual Information ────────────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 2 — MUTUAL INFORMATION vs winner/loser")
print("=" * 60)
from sklearn.feature_selection import mutual_info_classif

mi = mutual_info_classif(X, y_win, random_state=42)
for feat, score in sorted(zip(FEATURES, mi), key=lambda x: -x[1]):
    print(f"  {feat:<22} MI={score:.6f}")

# ── SECTION 3: Logistic Regression Coefficients ──────────────────────────────
print("\n" + "=" * 60)
print("SECTION 3 — LOGISTIC REGRESSION COEFFICIENTS")
print("=" * 60)
scaler = StandardScaler()
X_sc = scaler.fit_transform(X)

lr = LogisticRegression(max_iter=1000, random_state=42)
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
lr_auc = cross_val_score(lr, X_sc, y_win, cv=cv, scoring='roc_auc').mean()
lr.fit(X_sc, y_win)

print(f"  Cross-validated AUC: {lr_auc:.4f}")
print(f"\n  {'Feature':<22} {'Coefficient':>12}")
print("  " + "-" * 36)
coefs = sorted(zip(FEATURES, lr.coef_[0]), key=lambda x: -abs(x[1]))
for feat, coef in coefs:
    direction = "+win" if coef > 0 else "-win"
    print(f"  {feat:<22} {coef:>+12.4f}  {direction}")

# ── SECTION 4: Random Forest Feature Importance ──────────────────────────────
print("\n" + "=" * 60)
print("SECTION 4 — RANDOM FOREST FEATURE IMPORTANCE")
print("=" * 60)
rf = RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)
rf_auc = cross_val_score(rf, X, y_win, cv=cv, scoring='roc_auc').mean()
rf.fit(X, y_win)

print(f"  Cross-validated AUC: {rf_auc:.4f}")
print(f"\n  {'Feature':<22} {'Importance':>12}")
print("  " + "-" * 36)
importances = sorted(zip(FEATURES, rf.feature_importances_), key=lambda x: -x[1])
for feat, imp in importances:
    print(f"  {feat:<22} {imp:>12.4f}")

# ── SECTION 5: Permutation Importance ────────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 5 — PERMUTATION IMPORTANCE (Random Forest)")
print("=" * 60)
perm = permutation_importance(rf, X, y_win, n_repeats=30, random_state=42, n_jobs=-1)
perm_mean = perm.importances_mean
perm_std  = perm.importances_std

perm_sorted = sorted(zip(FEATURES, perm_mean, perm_std), key=lambda x: -x[1])
print(f"\n  {'Feature':<22} {'Mean':>10} {'Std':>8}")
print("  " + "-" * 42)
for feat, mean, std in perm_sorted:
    sign = "HURTS" if mean < 0 else ("useful" if mean > 0.001 else "~none")
    print(f"  {feat:<22} {mean:>+10.4f} {std:>8.4f}  [{sign}]")

# ── SECTION 6: Ablation Study ─────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 6 — ABLATION STUDY (remove one feature at a time)")
print("=" * 60)
rf_all = RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)
auc_all = cross_val_score(rf_all, X, y_win, cv=cv, scoring='roc_auc').mean()
print(f"\n  ALL features AUC:  {auc_all:.4f}")
print()

ablation = []
for feat in FEATURES:
    X_drop = X.drop(columns=[feat])
    rf_drop = RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)
    auc_drop = cross_val_score(rf_drop, X_drop, y_win, cv=cv, scoring='roc_auc').mean()
    delta = auc_drop - auc_all
    sign = "HURTS (remove helps)" if delta > 0.005 else ("VALUABLE" if delta < -0.005 else "neutral")
    ablation.append((feat, auc_drop, delta))
    print(f"  minus {feat:<18} AUC={auc_drop:.4f}  delta={delta:+.4f}  [{sign}]")

ablation_df = pd.DataFrame(ablation, columns=['feature', 'auc_without', 'delta'])

# ── SECTION 7: Optimal Weight Proposal ───────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 7 — PROPOSED WEIGHTS FOR confidence_v2")
print("=" * 60)
print()
print("  Based on permutation importance (direction from LR coefficients):")
print()

# Combine permutation importance with logistic regression sign
lr_signs = {feat: coef for feat, coef in zip(FEATURES, lr.coef_[0])}
perm_dict = {feat: mean for feat, mean, std in perm_sorted}

# Normalise to sum=1 (absolute value of positive-direction features)
useful = {f: max(0, perm_dict[f]) for f in FEATURES}
total = sum(useful.values())
if total > 0:
    weights = {f: v/total for f, v in useful.items()}
else:
    weights = {f: 1/len(FEATURES) for f in FEATURES}

weights_sorted = sorted(weights.items(), key=lambda x: -x[1])
for feat, w in weights_sorted:
    direction = "+" if lr_signs.get(feat, 0) > 0 else "-"
    perm_val = perm_dict.get(feat, 0)
    print(f"  {direction}{feat:<22}  weight={w:.3f}  (perm_imp={perm_val:+.4f})")

print()
print("  Confidence_v2 formula (unnormalized, for sigmoid):")
terms = []
for feat, w in weights_sorted:
    direction = 1 if lr_signs.get(feat, 0) >= 0 else -1
    if abs(w) > 0.01:
        terms.append(f"    {'+' if direction>0 else '-'} {feat} * {w*10:.2f}")
print("\n".join(terms))
