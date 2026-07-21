"""
PM Gate -- Decision-Time Causal Replay
========================================
Validates the PM V2 replacement rule using ONLY information available
at the exact eviction timestamp (T).

DECISION VARIABLES (causal, T-available only):
  - weakest_open.entry_score      (candidate score at time of entry -- same scale as incoming)
  - weakest_open.age_minutes      (T - entry_time)
  - incoming.score                (score_before_filters at T, from candidate log)
  - incoming.rank                 (must be 1)
  - portfolio_full                (implicit -- Rank 1 was rejected)

SIGNAL SCALE ALIGNMENT:
  Both incumbent and incoming use score_before_filters from the candidate log.
  The trade confidence column is NOT used for the comparison (different scale).

PM V2 RULE:
  IF portfolio_full
  AND incoming.rank == 1
  AND incoming.score > weakest_open.entry_score + SCORE_MARGIN
  AND weakest_open.age_minutes > MIN_AGE_MINUTES
  THEN replace

OUTCOME MEASUREMENT (ex-post, for evaluation only):
  - evicted position PnL: pro-rated remaining fraction from T to natural exit
  - candidate PnL: realized PnL of nearest forward matched trade (<= 2h)
  - net_delta = candidate_pnl - evicted_cost  (what we gain vs. baseline)
  - baseline: keeping evicted position to natural exit

PARAMETER GRID:
  MIN_AGE_MINUTES: [5, 15, 30]
  SCORE_MARGIN:    [0.0, 0.001, 0.003]
"""

import json
import pandas as pd
import numpy as np
import glob
from collections import defaultdict
from itertools import product

print("=" * 60)
print("PM GATE -- DECISION-TIME CAUSAL REPLAY")
print("=" * 60)

# ----------------------------------------------------------------
# 1. Load trade history
# ----------------------------------------------------------------
print("\n1. Loading trade history...")
snap = pd.read_csv('research/trades_snapshot.csv')
snap['created_at'] = pd.to_datetime(snap['created_at'], utc=True)
snap = snap.sort_values('created_at').reset_index(drop=True)

buys  = snap[snap['side'] == 'buy'].copy()
sells = snap[snap['side'] == 'sell'].copy()

# Build round-trip table
trips = buys.merge(
    sells[['trade_uuid', 'created_at', 'pnl']],
    on='trade_uuid', suffixes=('_buy', '_sell'), how='inner'
)
trips = trips.rename(columns={
    'created_at_buy':  'entry_time',
    'created_at_sell': 'exit_time',
    'pnl_sell':        'full_pnl',
})
trips['hold_seconds'] = (trips['exit_time'] - trips['entry_time']).dt.total_seconds()
print(f"   Round-trip trades: {len(trips)}")

# Stats on hold times
ht = trips['hold_seconds'] / 60.0
print(f"   Hold time: median={ht.median():.0f}m  p75={ht.quantile(0.75):.0f}m  max={ht.max():.0f}m")

def get_open_at(t):
    return trips[(trips['entry_time'] <= t) & (trips['exit_time'] > t)].copy()

# ----------------------------------------------------------------
# 2. Parse candidate logs
#    Build: (a) per-symbol entry score map keyed by (symbol, ts)
#           (b) approved set per cycle
#           (c) Rank 1 events
# ----------------------------------------------------------------
print("\n2. Parsing candidate logs...")

LOG_FILES = sorted(glob.glob('logs/candidates/candidates_*.jsonl'))

# All ranked events: {(cid, sym): {score, strength, ts, rank}}
ranked_events = {}
# Approved events
cycle_approved = defaultdict(set)
# Rank 1 events for "rejected" detection
cycle_rank1 = {}

for path in LOG_FILES:
    with open(path, encoding='utf-8') as f:
        for line in f:
            if 'candidate' not in line:
                continue
            try:
                obj   = json.loads(line)
                event = obj['event_type']
                p     = obj['payload']
                cid   = p.get('cycle_id')
                sym   = p.get('symbol')
                if not cid or not sym:
                    continue
                ts = pd.Timestamp(obj['timestamp'], tz='UTC')

                if event == 'candidate_ranked':
                    score = p.get('score_before_filters') or p.get('score_after_filters') or 0.0
                    ranked_events[(cid, sym)] = {
                        'score':    score,
                        'strength': p.get('strength') or 0.0,
                        'ts':       ts,
                        'rank':     p.get('rank'),
                    }
                    if p.get('rank') == 1:
                        cycle_rank1[cid] = {
                            'symbol':   sym,
                            'score':    score,
                            'strength': p.get('strength') or 0.0,
                            'ts':       ts,
                            'cycle_id': cid,
                        }

                elif event == 'candidate_approved':
                    cycle_approved[cid].add(sym)

            except Exception:
                pass

# Rejected Rank-1: ranked 1 but not approved
rejected = []
for cid, info in cycle_rank1.items():
    if info['symbol'] not in cycle_approved.get(cid, set()):
        rejected.append(info)

df_rej = pd.DataFrame(rejected).sort_values('ts').reset_index(drop=True)
print(f"   Rank 1 rejected events: {len(df_rej)}")

# ----------------------------------------------------------------
# 3. Build incumbent score lookup
#    For every buy trade, find the nearest candidate_ranked event
#    for that symbol within +-3 minutes -> get score_before_filters
# ----------------------------------------------------------------
print("\n3. Building incumbent entry score lookup...")

# Collect all ranked events as a DataFrame for merge
ranked_list = [
    {'symbol': sym, 'ts': v['ts'], 'entry_score': v['score']}
    for (cid, sym), v in ranked_events.items()
    if v['score'] is not None and v['score'] > 0
]
df_ranked = pd.DataFrame(ranked_list).sort_values('ts').reset_index(drop=True)

# For each buy trade, find the closest ranked event for the same symbol
trips = trips.sort_values('entry_time').reset_index(drop=True)

entry_scores = []
for _, row in trips.iterrows():
    sym  = row['symbol']
    t    = row['entry_time']
    mask = (df_ranked['symbol'] == sym)
    sub  = df_ranked[mask]
    if len(sub) == 0:
        entry_scores.append(np.nan)
        continue
    dt = (sub['ts'] - t).dt.total_seconds().abs()
    best_idx = dt.idxmin()
    if dt[best_idx] <= 300:  # within 5 minutes
        entry_scores.append(sub.loc[best_idx, 'entry_score'])
    else:
        entry_scores.append(np.nan)

trips['entry_score'] = entry_scores
matched_score = trips['entry_score'].notna().sum()
print(f"   Incumbents with matched entry score: {matched_score}/{len(trips)}")
print(f"   Score distribution: mean={trips['entry_score'].mean():.4f}  "
      f"p25={trips['entry_score'].quantile(0.25):.4f}  "
      f"p75={trips['entry_score'].quantile(0.75):.4f}")

# ----------------------------------------------------------------
# 4. Match forward candidate PnL for rejected Rank 1 events
# ----------------------------------------------------------------
print("\n4. Matching forward candidate trades...")

matched_parts = []
for sym in df_rej['symbol'].unique():
    sym_rej  = df_rej[df_rej['symbol'] == sym].copy()
    sym_buys = buys[buys['symbol'] == sym].copy()
    if len(sym_buys) == 0:
        continue
    merged = pd.merge_asof(
        sym_rej.sort_values('ts'),
        sym_buys.drop(columns=['symbol'])
               .rename(columns={'created_at': 'trade_ts'})
               .sort_values('trade_ts'),
        left_on='ts',
        right_on='trade_ts',
        direction='forward',
        tolerance=pd.Timedelta('2h'),
    )
    matched_parts.append(merged)

if matched_parts:
    cand_matched = pd.concat(matched_parts, ignore_index=True)
    cand_matched = cand_matched.merge(
        sells[['trade_uuid', 'pnl']].rename(columns={'pnl': 'cand_pnl'}),
        on='trade_uuid', how='left'
    )
else:
    cand_matched = df_rej.copy()
    cand_matched['cand_pnl'] = np.nan

cand_matched = cand_matched[cand_matched['cand_pnl'].notna()].copy()
print(f"   Rank 1 rejections with forward PnL: {len(cand_matched)}")

# ----------------------------------------------------------------
# 5. Diagnostics
# ----------------------------------------------------------------
print("\n5. Diagnostics (age & score distributions at rejection time)...")

sample = cand_matched.head(100)
ages_at_t   = []
scores_at_t = []
n_open      = []
n_eligible_5 = 0
for _, row in sample.iterrows():
    T = row['ts']
    open_pos = get_open_at(T)
    if len(open_pos) == 0:
        n_open.append(0)
        continue
    n_open.append(len(open_pos))
    ages = ((T - open_pos['entry_time']).dt.total_seconds() / 60.0).tolist()
    ages_at_t.extend(ages)
    scores_at_t.extend(open_pos['entry_score'].dropna().tolist())
    if any(a >= 5 for a in ages):
        n_eligible_5 += 1

print(f"   Events with open positions (n=100 sample): {sum(1 for x in n_open if x > 0)}/100")
if n_open:
    print(f"   Avg open positions at rejection: {np.mean([x for x in n_open if x > 0]):.1f}")
if ages_at_t:
    print(f"   Age of incumbents: min={min(ages_at_t):.1f}m "
          f"p25={np.percentile(ages_at_t,25):.1f}m "
          f"p50={np.percentile(ages_at_t,50):.1f}m "
          f"p75={np.percentile(ages_at_t,75):.1f}m "
          f"max={max(ages_at_t):.1f}m")
    print(f"   Events with >=1 position older than 5min: {n_eligible_5}/100")
if scores_at_t:
    print(f"   Incumbent entry scores: min={min(scores_at_t):.5f} "
          f"mean={np.mean(scores_at_t):.5f} "
          f"max={max(scores_at_t):.5f}")

cand_scores = cand_matched['score'].dropna()
if len(cand_scores) > 0:
    print(f"   Candidate (Rank 1) scores: min={cand_scores.min():.5f} "
          f"mean={cand_scores.mean():.5f} "
          f"max={cand_scores.max():.5f}")

# ----------------------------------------------------------------
# 6. Parameter grid sweep
# ----------------------------------------------------------------
print("\n6. Running causal decision-time parameter sweep...")
print("-" * 60)

MIN_AGES     = [5, 15, 30]
SCORE_MARGINS = [0.0, 0.001, 0.003]

results = []

for min_age, margin in product(MIN_AGES, SCORE_MARGINS):

    baseline_pnl    = []
    replacement_pnl = []
    replacements    = 0
    turnover        = 0

    for _, row in cand_matched.iterrows():
        T          = row['ts']
        cand_score = float(row.get('score') or 0.0)
        cand_pnl   = float(row['cand_pnl'])

        open_pos = get_open_at(T)
        if len(open_pos) == 0:
            continue

        open_pos = open_pos.copy()
        open_pos['age_min'] = (T - open_pos['entry_time']).dt.total_seconds() / 60.0

        eligible = open_pos[
            (open_pos['age_min'] >= min_age) &
            (open_pos['entry_score'].notna())
        ]

        if len(eligible) == 0:
            # No eligible positions -- keep baseline (use worst open position's PnL)
            baseline_pnl.append(float(open_pos.iloc[0]['full_pnl']))
            turnover += 1
            continue

        weakest = eligible.loc[eligible['entry_score'].idxmin()]
        w_score = float(weakest['entry_score'])
        w_age   = float(weakest['age_min'])

        # ---- CAUSAL DECISION (only T-available info) ----
        replace_decision = (
            cand_score > w_score + margin
            and w_age >= min_age
        )
        # -------------------------------------------------

        if replace_decision:
            w_hold    = float(weakest['hold_seconds'])
            w_remain  = (weakest['exit_time'] - T).total_seconds()
            w_fraction = w_remain / w_hold if w_hold > 0 else 1.0
            evicted_cost = float(weakest['full_pnl']) * w_fraction

            net = cand_pnl - evicted_cost
            replacement_pnl.append(net)
            baseline_pnl.append(float(weakest['full_pnl']))
            replacements += 1
        else:
            baseline_pnl.append(float(weakest['full_pnl']))

        turnover += 1

    if len(replacement_pnl) == 0:
        results.append({
            'min_age_min': min_age, 'score_margin': margin,
            'replacements': 0, 'replacement_rate_pct': 0.0,
            'baseline_exp': np.nan, 'replacement_exp': np.nan,
            'exp_improvement_pct': np.nan,
            'baseline_total': np.nan, 'replacement_total': np.nan,
            'baseline_winrate': np.nan, 'replacement_winrate': np.nan,
        })
        continue

    b_arr = np.array(baseline_pnl)
    r_arr = np.array(replacement_pnl)
    b_exp = b_arr.mean()
    r_exp = r_arr.mean()
    exp_imp = (r_exp - b_exp) / abs(b_exp) * 100 if b_exp != 0 else np.nan
    rep_rate = replacements / turnover * 100 if turnover > 0 else 0

    results.append({
        'min_age_min':           min_age,
        'score_margin':          margin,
        'replacements':          replacements,
        'replacement_rate_pct':  round(rep_rate, 1),
        'baseline_exp':          round(b_exp, 7),
        'replacement_exp':       round(r_exp, 7),
        'exp_improvement_pct':   round(exp_imp, 1),
        'baseline_total':        round(b_arr.sum(), 4),
        'replacement_total':     round(r_arr.sum(), 4),
        'baseline_winrate':      round((b_arr > 0).mean() * 100, 1),
        'replacement_winrate':   round((r_arr > 0).mean() * 100, 1),
    })

df_results = pd.DataFrame(results)
print(df_results.to_string(index=False))

print("\n" + "=" * 60)
valid = df_results[df_results['replacements'] > 0]
if len(valid) == 0:
    print("RESULT: No replacements triggered in any parameter combination.")
    print("CAUSE:  Incumbent scores not matched OR all positions too young.")
    print("PM GATE: INCONCLUSIVE")
else:
    best = valid.loc[valid['exp_improvement_pct'].idxmax()]
    print("SUMMARY -- Best performing parameter set:")
    print(f"  min_age_minutes = {best['min_age_min']}")
    print(f"  score_margin    = {best['score_margin']}")
    print(f"  replacements    = {best['replacements']} ({best['replacement_rate_pct']}% of events)")
    print(f"  Baseline expectancy:    {best['baseline_exp']:.7f}")
    print(f"  Replacement expectancy: {best['replacement_exp']:.7f}")
    print(f"  Improvement:            {best['exp_improvement_pct']:+.1f}%")
    print(f"  Baseline total PnL:     {best['baseline_total']:.4f}")
    print(f"  Replacement total PnL:  {best['replacement_total']:.4f}")
    print(f"  Baseline win rate:      {best['baseline_winrate']}%")
    print(f"  Replacement win rate:   {best['replacement_winrate']}%")
    print("=" * 60)

    gate_pass = best['exp_improvement_pct'] >= 20.0
    print(f"\nPM GATE: {'PASS -- improvement >= 20%' if gate_pass else 'FAIL -- improvement < 20%'}")
    print(f"PM V2 APPROVED: {gate_pass}")
