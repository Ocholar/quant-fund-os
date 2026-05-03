import math, random, time

class PaperMarketData:
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.t = 0
        self.prices = {s: 100 + random.random() * 50 for s in symbols}

    def tick(self):
        self.t += 1
        out = {}
        for s in self.symbols:
            drift = 0.0002 * math.sin(self.t / 30)
            shock = random.gauss(0, 0.006)
            self.prices[s] *= (1 + drift + shock)
            out[s] = max(self.prices[s], 0.01)
        return {"ts": int(time.time()), "prices": out}
