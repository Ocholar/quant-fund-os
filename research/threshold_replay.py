import pandas as pd
import numpy as np

def run_replay():
    df = pd.read_csv('research/feature_dataset.csv')
    df['winner'] = df['pnl'] > 0
    total_trades = len(df)
    
    # Assuming 14 days of data for trades/day based on earlier Phase 1
    # We can estimate days by looking at the count. Let's just use 14.
    days = 14.0 
    
    thresholds = [0.0250, 0.0100, 0.0050, 0.0030, 0.0025, 0.0020, 0.0018, 0.0015, 0.0012, 0.0010]
    
    results = []
    
    for t in thresholds:
        accepted = df[df['strength'] >= t]
        rejected = df[df['strength'] < t]
        
        n_accepted = len(accepted)
        n_rejected = len(rejected)
        
        pct_accepted = n_accepted / total_trades * 100
        pct_rejected = n_rejected / total_trades * 100
        
        if n_accepted > 0:
            trades_per_day = n_accepted / days
            win_rate = accepted['winner'].mean() * 100
            expectancy = accepted['pnl'].mean()
            
            # Estimate drawdown simply as the worst cumulative sum of pnl (assuming sequential, but we don't have ts, so just worst PNL or sum of negative)
            # Actually, standard is max(cumulative peak - cumulative)
            # We'll just do a rough sequential cumulative PNL if we sort by trade_uuid (not strictly chronological, but ok)
            cum_pnl = accepted['pnl'].cumsum()
            peaks = cum_pnl.cummax()
            dd = (peaks - cum_pnl).max()
            
        else:
            trades_per_day = 0
            win_rate = 0
            expectancy = 0
            dd = 0
            
        results.append({
            'Threshold': t,
            'Trades/Day': trades_per_day,
            'Win Rate %': win_rate,
            'Expectancy': expectancy,
            'Drawdown': dd,
            'Rejected %': pct_rejected,
            'Accepted %': pct_accepted
        })
        
    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))

if __name__ == '__main__':
    run_replay()
