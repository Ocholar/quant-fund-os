from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Portfolio:
    """
    Portfolio accounting model.

    Agent 2 invariant:
        equity = cash + market value of open positions
        total_pnl = realized_pnl + unrealized_pnl
        drawdown = current equity versus peak equity

    Clean reset invariant:
        equity = starting_equity
        cash = starting_equity
        exposure = 0
        positions = {}
        realized_pnl = 0
        unrealized_pnl = 0
        drawdown = 0
        peak = starting_equity
    """

    cash: float = 100.0
    positions: dict[str, float] = field(default_factory=dict)
    avg_entry: dict[str, float] = field(default_factory=dict)
    realized_pnl: float = 0.0
    equity: float | None = None
    peak: float | None = None
    unrealized_pnl: float = 0.0

    def __post_init__(self) -> None:
        self.cash = float(self.cash)

        if self.equity is None:
            self.equity = float(self.cash)
        else:
            self.equity = float(self.equity)

        if self.peak is None:
            self.peak = float(self.equity)
        else:
            self.peak = float(self.peak)

        self.realized_pnl = float(self.realized_pnl)
        self.unrealized_pnl = float(self.unrealized_pnl)

    def reset(self, starting_equity: float = 100.0) -> None:
        """
        Clean paper reset.

        This intentionally clears peak/drawdown memory.
        After reset, stale 94.75 / -5.25% state must not survive.
        """
        base = float(starting_equity)
        self.cash = base
        self.positions.clear()
        self.avg_entry.clear()
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.equity = base
        self.peak = base

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """
        Recalculate equity from current cash and current open positions only.

        Missing or invalid prices are ignored instead of creating fake exposure.
        """
        market_value = 0.0
        unrealized = 0.0

        for sym, qty_raw in list(self.positions.items()):
            qty = float(qty_raw or 0.0)

            if qty <= 0:
                self.positions.pop(sym, None)
                self.avg_entry.pop(sym, None)
                continue

            price = float(prices.get(sym, 0.0) or 0.0)

            if price <= 0:
                continue

            market_value += qty * price

            entry = float(self.avg_entry.get(sym, price) or price)
            unrealized += qty * (price - entry)

        self.unrealized_pnl = float(unrealized)
        self.equity = float(self.cash) + float(market_value)

        if self.peak is None or self.peak <= 0:
            self.peak = float(self.equity)
        else:
            self.peak = max(float(self.peak), float(self.equity))

        return float(self.equity)

    @property
    def exposure(self) -> float:
        """
        Open market exposure in account currency.

        This uses equity - cash because this simple model stores positions as
        quantities and relies on mark_to_market to refresh equity.
        """
        return max(0.0, float(self.equity or 0.0) - float(self.cash or 0.0))

    @property
    def exposure_pct(self) -> float:
        equity = float(self.equity or 0.0)
        if equity <= 0:
            return 0.0
        return self.exposure / equity

    @property
    def total_pnl(self) -> float:
        return float(self.realized_pnl) + float(self.unrealized_pnl)

    @property
    def drawdown(self) -> float:
        peak = float(self.peak or 0.0)
        equity = float(self.equity or 0.0)

        if peak <= 0:
            return 0.0

        return (equity - peak) / peak

    def can_sell_quantity(self, symbol: str, quantity: float) -> bool:
        """
        Spot invariant:
        sell quantity can never exceed current open quantity.
        """
        open_qty = float(self.positions.get(symbol, 0.0) or 0.0)
        requested_qty = float(quantity or 0.0)
        return requested_qty >= 0 and requested_qty <= open_qty + 1e-12

    def assert_invariants(self) -> None:
        if float(self.cash) < -1e-9:
            raise ValueError(f"portfolio invariant failed: negative cash {self.cash}")

        if self.peak is None or float(self.peak) <= 0:
            raise ValueError("portfolio invariant failed: peak equity must be positive")

        for sym, qty in self.positions.items():
            if float(qty) < -1e-12:
                raise ValueError(f"portfolio invariant failed: negative position {sym}={qty}")

        if self.exposure_pct < -1e-12:
            raise ValueError("portfolio invariant failed: negative exposure_pct")
