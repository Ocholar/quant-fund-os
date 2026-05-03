from core.metrics import sharpe_ratio, max_drawdown

def test_metrics_basic():
    assert sharpe_ratio([0.01, 0.02, -0.01]) != 0
    assert max_drawdown([100, 110, 90]) < 0
