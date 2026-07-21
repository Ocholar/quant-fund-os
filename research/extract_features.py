"""
Feature Attribution — Vectorized Time+Symbol Join using merge_asof
Much faster than per-row loop.
"""
import json
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import os

FEATURE_COLS = ['trend', 'long_trend', 'one_tick_momentum',
                'breakout_score', 'trend_quality', 'volatility_log',
                'strength', 'confidence_log']

LOG_FILES = [
    '/app/logs/candidates/candidates_2026-07-14.jsonl',
    '/app/logs/candidates/candidates_2026-07-15.jsonl',
    '/app/logs/candidates/candidates_2026-07-16.jsonl',
]

# ── 1. Stream candidate logs, keep only RANKED rows with real features ─────────
print("Loading candidate logs (streaming)...")
ranked_rows = []
for path in LOG_FILES:
    print(f"  {path}")
    with open(path) as f:
        for line in f:
            obj = json.loads(line)
            if obj['event_type'] != 'candidate_ranked':
                continue
            p = obj['payload']
            feat = p.get('features') or {}
            if feat.get('trend') is None:
                continue
            ranked_rows.append({
                'symbol':            p['symbol'],
                'ts':                pd.Timestamp(obj['timestamp']),
                'confidence_log':    float(p.get('confidence') or 0),
                'strength':          float(p.get('strength') or 0),
                'volatility_log':    float(p.get('volatility') or 0),
                'trend':             float(feat.get('trend') or 0),
                'long_trend':        float(feat.get('long_trend') or 0),
                'one_tick_momentum': float(feat.get('one_tick_momentum') or 0),
                'breakout_score':    float(feat.get('breakout_score') or 0),
                'trend_quality':     float(feat.get('trend_quality') or 0),
            })

ranked = pd.DataFrame(ranked_rows)
ranked['ts'] = ranked['ts'].dt.tz_convert('UTC')
ranked = ranked.sort_values('ts').reset_index(drop=True)
print(f"  {len(ranked):,} candidate rows loaded")

# ── 2. Load trades ────────────────────────────────────────────────────────────
db_url = os.environ.get('DATABASE_URL',
    'postgresql+psycopg2://qfos:qfos_password@postgres:5432/quant_fund_os')
engine = create_engine(db_url)

with engine.connect() as conn:
    trades = pd.read_sql(text(
        "SELECT trade_uuid, symbol, side, confidence, pnl, mfe, mae, "
        "exit_reason, strategy, regime, created_at FROM trades ORDER BY created_at"
    ), conn)

trades['created_at'] = pd.to_datetime(trades['created_at'], utc=True)
print(f"  {len(trades)} trades loaded")

buys  = trades[trades['side'] == 'buy'].drop(columns=['pnl','mfe','mae','exit_reason'], errors='ignore').copy().sort_values('created_at').reset_index(drop=True)
sells = trades[trades['side'] == 'sell'].copy()

# ── 3. Vectorized merge_asof per symbol ──────────────────────────────────────
print("Joining features to BUY trades (merge_asof per symbol)...")
matched_parts = []

for sym in buys['symbol'].unique():
    sym_buys    = buys[buys['symbol'] == sym].copy()
    sym_ranked  = ranked[ranked['symbol'] == sym].copy()
    if len(sym_ranked) == 0:
        continue

    # merge_asof: for each buy timestamp, find nearest past candidate entry
    merged = pd.merge_asof(
        sym_buys.rename(columns={'created_at': 'ts'}),
        sym_ranked[['ts'] + FEATURE_COLS],
        on='ts',
        direction='nearest',
        tolerance=pd.Timedelta('5min'),
    )
    merged = merged.rename(columns={'ts': 'created_at'})
    matched_parts.append(merged)

if not matched_parts:
    print("ERROR: No matches found!")
    exit(1)

buy_feat = pd.concat(matched_parts, ignore_index=True)
matched_count = buy_feat[FEATURE_COLS[0]].notna().sum()
print(f"  BUY trades with matched features: {matched_count} / {len(buys)}")

# ── 4. Join to SELL outcomes via trade_uuid ───────────────────────────────────
sell_out = sells[['trade_uuid', 'pnl', 'mfe', 'mae', 'exit_reason', 'strategy', 'created_at']].copy()
sell_out = sell_out.rename(columns={'strategy': 'exit_strategy', 'created_at': 'exit_at'})

completed = buy_feat.merge(sell_out, on='trade_uuid', how='inner')
print(f"  Completed (buy+sell) trades: {len(completed)}")

# ── 5. Save ───────────────────────────────────────────────────────────────────
out_cols = ['trade_uuid', 'symbol', 'regime', 'strategy', 'confidence',
            'trend', 'long_trend', 'one_tick_momentum', 'breakout_score',
            'trend_quality', 'volatility_log', 'strength', 'confidence_log',
            'pnl', 'mfe', 'mae', 'exit_reason', 'exit_strategy']
completed[[c for c in out_cols if c in completed.columns]].to_csv(
    '/app/feature_dataset.csv', index=False
)
print("Saved /app/feature_dataset.csv")
print()
desc_cols = [c for c in ['trend','long_trend','one_tick_momentum','breakout_score',
                          'trend_quality','volatility_log','confidence','pnl'] if c in completed.columns]
print(completed[desc_cols].describe().to_string())
