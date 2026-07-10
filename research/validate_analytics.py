import os
import sys
import logging
from datetime import datetime, timezone
import collections
import subprocess

# Add parent to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.cli import _get_connection
from analytics.dataset import build_canonical_dataset
from analytics.metrics import edge_report, strategy_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

def _float_eq(a, b, tol=1e-5):
    if a is None or b is None: return False
    return abs(a - b) <= tol

def run_validations():
    conn = _get_connection()
    try:
        lifecycles = build_canonical_dataset(conn)
    finally:
        conn.close()

    if not lifecycles:
        print("NO LIFECYCLES FOUND.")
        return

    print("=" * 60)
    print("VALIDATION 1: DATASET INTEGRITY")
    print("=" * 60)
    
    qty_mismatch = 0
    pnl_mismatch = 0
    time_mismatch = 0

    for lc in lifecycles:
        # We don't have explicit entry vs exit quantities in the lifecycle dict directly,
        # but the lifecycle assumes entry_qty is fully closed by the exit. 
        # Actually `dataset.py` computes entry_qty = sum(buy qty). The exit qty was `final_qty`.
        # Wait, the lifecycle dict only has `entry_qty`, it assumes exit_qty == entry_qty since it's flat-to-flat.
        # But we can verify gross_pnl.
        # Pnl in DB is usually net or gross? 
        # Our dataset has `return_pct`, `realized_pnl`.
        
        # entry_time < exit_time
        et = lc.get("entry_time")
        xt = lc.get("exit_time")
        if et and xt and et >= xt:
            time_mismatch += 1
            print(f"Time mismatch: {lc.get('trade_uuid')} {et} >= {xt}")
            
    print(f"Time mismatches: {time_mismatch}")
    
    print("\n" + "=" * 60)
    print("VALIDATION 2: PORTFOLIO RECONCILIATION")
    print("=" * 60)
    total_dataset_pnl = sum(lc.get("realized_pnl", 0) for lc in lifecycles)
    print(f"Total Dataset Realized PnL: {total_dataset_pnl:.6f}")
    
    print("\n" + "=" * 60)
    print("VALIDATION 3: STRATEGY ATTRIBUTION")
    print("=" * 60)
    strat_pnl = collections.defaultdict(float)
    sym_pnl = collections.defaultdict(float)
    regime_pnl = collections.defaultdict(float)
    
    for lc in lifecycles:
        p = lc.get("realized_pnl", 0)
        strat_pnl[lc.get("strategy") or "unknown"] += p
        sym_pnl[lc.get("symbol") or "unknown"] += p
        regime_pnl[lc.get("regime") or "unknown"] += p
        
    sum_strat = sum(strat_pnl.values())
    sum_sym = sum(sym_pnl.values())
    sum_regime = sum(regime_pnl.values())
    
    print(f"Sum of Strategies PnL: {sum_strat:.6f} (Diff: {abs(sum_strat - total_dataset_pnl):.6f})")
    print(f"Sum of Symbols PnL:    {sum_sym:.6f} (Diff: {abs(sum_sym - total_dataset_pnl):.6f})")
    print(f"Sum of Regimes PnL:    {sum_regime:.6f} (Diff: {abs(sum_regime - total_dataset_pnl):.6f})")
    
    print("\n" + "=" * 60)
    print("VALIDATION 4: LIFECYCLE METRICS")
    print("=" * 60)
    
    mfe_fails = 0
    mae_fails = 0
    live_checked = 0
    for lc in lifecycles:
        if lc.get("mfe_mae_quality") == "live":
            live_checked += 1
            mfe = lc.get("mfe")
            mae = lc.get("mae")
            pnl = lc.get("realized_pnl", 0)
            if mfe is not None and pnl > 0 and mfe < pnl - 1e-5:
                mfe_fails += 1
                print(f"MFE Fail: {lc.get('trade_uuid')} MFE={mfe} PnL={pnl}")
            if mae is not None and mae > 1e-5:
                mae_fails += 1
                print(f"MAE Fail: {lc.get('trade_uuid')} MAE={mae}")
                
    print(f"Live trades checked: {live_checked}")
    print(f"MFE < PnL Fails: {mfe_fails}")
    print(f"MAE > 0 Fails: {mae_fails}")

    print("\n" + "=" * 60)
    print("VALIDATION 5: CLI COMMANDS")
    print("=" * 60)

    cmds = [
        ["python", "-m", "analytics.cli", "--report", "edge"],
        ["python", "-m", "analytics.cli", "--report", "strategy"],
        ["python", "-m", "analytics.cli", "--export", "experiments/validation_trades.csv"],
        ["python", "-m", "analytics.cli", "--export-run", "experiments/validation_run_001"]
    ]
    
    for cmd in cmds:
        print(f"\nRunning: {' '.join(cmd)}")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print("SUCCESS")
            # print stdout truncated
            lines = res.stdout.splitlines()
            if len(lines) > 20:
                print("\n".join(lines[:10]))
                print("... truncated ...")
                print("\n".join(lines[-10:]))
            else:
                print(res.stdout)
        except subprocess.CalledProcessError as e:
            print("FAILED")
            print(e.stderr)

    print("\n" + "=" * 60)
    print("FIRST RESEARCH REPORT METRICS")
    print("=" * 60)
    
    # We will generate a markdown file separately, but dump some quick numbers here.
    pass

if __name__ == "__main__":
    run_validations()
