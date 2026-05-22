class Portfolio:
    def __init__(self, cash=100.0):
        self.cash = cash
        self.positions = {}
        self.equity = cash
        self.peak = cash

    def mark_to_market(self, prices: dict[str, float]):
        value = self.cash + sum(qty * prices.get(sym, 0) for sym, qty in self.positions.items())
        self.equity = value
        self.peak = max(self.peak, value)
        return value

    @property
    def drawdown(self):
        return 0 if self.peak == 0 else (self.equity - self.peak) / self.peak
