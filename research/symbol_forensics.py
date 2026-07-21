import pandas as pd
import numpy as np

def analyze_symbols():
    df = pd.read_csv('C:/Users/Administrator/Documents/quant-fund-os/research/trades_snapshot.csv')
    
    # Filter to completed trades only
    # In earlier tasks we defined completed as side == 'SELL' or pnl IS NOT NULL
    # Using pnl IS NOT NULL is robust
    completed = df[df['pnl'].notnull()].copy()
    
    target_symbols = ['BILL/USDT', 'TRIA/USDT', 'BDX/USDT', 'ULTIMA/USDT']
    
    # Filter to our symbols
    filtered = completed[completed['symbol'].isin(target_symbols)].copy()
    
    # Calculate holding duration
    # We need to map buy times for sells.
    # Actually, the dataset might contain 'created_at', 'entry_ts', 'exit_ts' or we can compute from age.
    # We can check columns first.
    
    print("Columns available:", list(df.columns))
    
    # If holding duration isn't precomputed, we might need to match BUY/SELL or use 'age_min'/'holding_time' if available.
    
    metrics = []
    
    for symbol in target_symbols:
        sym_trades = filtered[filtered['symbol'] == symbol]
        if len(sym_trades) == 0:
            continue
            
        # Try to find confidence from BUYs if SELLs don't have it, or maybe it's in the SELL record.
        # Based on task 003, we know how to map confidence.
        
        # We will extract: entry confidence, strategy, exit reason, holding duration, MFE, MAE, realized PnL
        
        # Let's print summary stats for these
        avg_conf = sym_trades['confidence'].mean() if 'confidence' in sym_trades else np.nan
        mfe_mean = sym_trades['mfe'].mean() if 'mfe' in sym_trades else np.nan
        mae_mean = sym_trades['mae'].mean() if 'mae' in sym_trades else np.nan
        pnl_mean = sym_trades['pnl'].mean()
        
        # Exit reason counts
        exit_reasons = sym_trades['exit_reason'].value_counts().to_dict() if 'exit_reason' in sym_trades else {}
        
        # Strategy counts
        strategies = sym_trades['strategy'].value_counts().to_dict()
        
        print(f"\\n=== {symbol} ===")
        print(f"Count: {len(sym_trades)}")
        print(f"Avg PnL: {pnl_mean:.6f}")
        print(f"Avg MFE: {mfe_mean:.6f}")
        print(f"Avg MAE: {mae_mean:.6f}")
        print(f"Avg Confidence: {avg_conf:.6f}")
        print(f"Strategies: {strategies}")
        print(f"Exit Reasons: {exit_reasons}")

if __name__ == "__main__":
    analyze_symbols()
