from core.config import settings
from core.portfolio import Portfolio
from core.risk_engine import RiskEngine


def _equity_for_drawdown(drawdown: float, peak: float = 100.0) -> float:
    return peak * (1.0 + float(drawdown))


def test_clean_reset_invariant():
    p = Portfolio(cash=94.75, equity=94.75, peak=100.0)
    p.positions["BTC/USDT"] = 0.01
    p.avg_entry["BTC/USDT"] = 60000.0
    p.realized_pnl = -5.25
    p.unrealized_pnl = -1.0

    p.reset(100.0)

    assert p.cash == 100.0
    assert p.equity == 100.0
    assert p.peak == 100.0
    assert p.positions == {}
    assert p.avg_entry == {}
    assert p.realized_pnl == 0.0
    assert p.unrealized_pnl == 0.0
    assert p.drawdown == 0.0
    assert p.exposure == 0.0
    assert p.exposure_pct == 0.0


def test_safe_after_clean_reset_can_buy():
    r = RiskEngine()
    r.reset_risk_state(100.0)

    decision = r.can_buy(
        equity=100.0,
        cash=100.0,
        exposure_pct=0.0,
        allocation_notional=1.0,
        peak_equity=100.0,
    )

    assert decision.approved is True
    assert decision.reason == "approved"
    assert decision.risk_status == "SAFE"


def test_stale_94_75_does_not_survive_clean_reset():
    r = RiskEngine()
    r.peak_equity = 100.0

    p = Portfolio(cash=94.75, equity=94.75, peak=100.0)
    assert p.drawdown < 0

    p.reset(100.0)
    r.reset_risk_state(100.0)

    decision = r.can_buy(
        equity=p.equity,
        cash=p.cash,
        exposure_pct=p.exposure_pct,
        allocation_notional=1.0,
        peak_equity=p.peak,
    )

    assert p.drawdown == 0.0
    assert decision.approved is True
    assert decision.reason == "approved"
    assert decision.risk_status == "SAFE"


def test_blocked_when_drawdown_really_violates_loaded_limit():
    r = RiskEngine()

    blocked_drawdown = float(settings.blocked_drawdown)
    test_drawdown = blocked_drawdown - 0.001
    test_equity = _equity_for_drawdown(test_drawdown, peak=100.0)

    status = r.risk_status(
        equity=test_equity,
        exposure_pct=0.0,
        peak_equity=100.0,
    )

    decision = r.can_buy(
        equity=test_equity,
        cash=test_equity,
        exposure_pct=0.0,
        allocation_notional=1.0,
        peak_equity=100.0,
    )

    assert status == "BLOCKED"
    assert decision.approved is False
    assert decision.reason.startswith("blocked_drawdown_")
    assert decision.risk_status == "BLOCKED"


def test_near_blocked_is_warning_zone_before_hard_blocked():
    r = RiskEngine()

    blocked_drawdown = float(settings.blocked_drawdown)
    near_drawdown = float(settings.near_blocked_drawdown)

    assert near_drawdown > blocked_drawdown

    midway_drawdown = (blocked_drawdown + near_drawdown) / 2.0
    midway_equity = _equity_for_drawdown(midway_drawdown, peak=100.0)

    decision = r.can_buy(
        equity=midway_equity,
        cash=midway_equity,
        exposure_pct=0.0,
        allocation_notional=1.0,
        peak_equity=100.0,
    )

    assert decision.approved is False
    assert decision.reason.startswith("near_blocked_drawdown_")


def test_total_exposure_blocks_safe_label():
    r = RiskEngine()

    status = r.risk_status(
        equity=100.0,
        exposure_pct=settings.max_total_exposure_pct + 0.001,
        peak_equity=100.0,
    )

    assert status == "BLOCKED"


def test_spot_sell_cannot_exceed_open_quantity():
    p = Portfolio(cash=99.0, equity=100.0, peak=100.0)
    p.positions["ETH/USDT"] = 0.01

    assert p.can_sell_quantity("ETH/USDT", 0.005) is True
    assert p.can_sell_quantity("ETH/USDT", 0.01) is True
    assert p.can_sell_quantity("ETH/USDT", 0.011) is False
    assert p.can_sell_quantity("BTC/USDT", 0.001) is False


def test_mark_to_market_updates_equity_peak_drawdown_and_unrealized():
    p = Portfolio(cash=95.0, equity=100.0, peak=100.0)
    p.positions["BTC/USDT"] = 0.001
    p.avg_entry["BTC/USDT"] = 5000.0

    equity = p.mark_to_market({"BTC/USDT": 6000.0})

    assert equity == 101.0
    assert p.equity == 101.0
    assert p.peak == 101.0
    assert p.unrealized_pnl == 1.0
    assert p.drawdown == 0.0
