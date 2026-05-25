import random
from dataclasses import dataclass

@dataclass
class StrategyDNA:
    name: str
    trend_threshold: float
    momentum_threshold: float
    risk_fraction: float
    shadow_mode: bool = False

    @staticmethod
    def random(name="evo"):
        return StrategyDNA(
            name=f"{name}_{random.randint(1000,9999)}",
            trend_threshold=random.uniform(-0.0001, 0.0001),
            momentum_threshold=random.uniform(-0.0001, 0.0001),
            risk_fraction=random.uniform(0.01, 0.03), # 1% to 3% max per trade
            shadow_mode=random.random() < 0.3, # 30% of new strategies start in shadow mode
        )

    def mutate(self):
        return StrategyDNA(
            name=f"{self.name}_m",
            trend_threshold=max(0.0005, self.trend_threshold * random.uniform(0.8, 1.2)),
            momentum_threshold=max(0.0005, self.momentum_threshold * random.uniform(0.8, 1.2)),
            risk_fraction=min(0.03, max(0.002, self.risk_fraction * random.uniform(0.8, 1.2))),
            shadow_mode=self.shadow_mode,
        )

class StrategyPool:
    def __init__(self, size=12):
        self.strategies = [StrategyDNA.random() for _ in range(size)]

    def generate_candidates(self, market_state):
        return self.strategies

    def score(self, candidates, features_by_symbol=None):
        scored = []
        for s in candidates:
            score = random.uniform(0.2, 0.8)
            scored.append({"strategy": s, "score": score})
        return sorted(scored, key=lambda x: x["score"], reverse=True)

    def evolve(self, scored):
        survivors = [x["strategy"] for x in scored[: max(2, len(scored)//3)]]
        children = []
        for s in survivors:
            children.append(s)
            children.append(s.mutate())
        self.strategies = children[:len(self.strategies)]
