from core.metrics import sharpe_ratio, max_drawdown

def run_backtest(price_series):
    equity = [100.0]
    returns = []
    for i in range(1, len(price_series)):
        r = price_series[i] / price_series[i-1] - 1
        returns.append(r)
        equity.append(equity[-1] * (1 + r * 0.25))
    return {"equity": equity, "sharpe": sharpe_ratio(returns), "max_drawdown": max_drawdown(equity)}
