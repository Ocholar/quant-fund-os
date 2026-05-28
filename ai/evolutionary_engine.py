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
        # Thresholds are intentionally positive. Near-zero thresholds caused the
        # bot to buy weak noise across almost the whole universe.
        return StrategyDNA(
            name=f"{name}_{random.randint(1000, 9999)}",
            trend_threshold=random.uniform(0.00005, 0.0015),
            momentum_threshold=random.uniform(0.00005, 0.0020),
            risk_fraction=random.uniform(0.020, 0.045),
            shadow_mode=False,
        )

    def mutate(self):
        return StrategyDNA(
            name=f"{self.name}_m",
            trend_threshold=min(0.0030, max(0.00005, self.trend_threshold * random.uniform(0.85, 1.15))),
            momentum_threshold=min(0.0040, max(0.00005, self.momentum_threshold * random.uniform(0.85, 1.15))),
            risk_fraction=min(0.050, max(0.015, self.risk_fraction * random.uniform(0.85, 1.15))),
            shadow_mode=False,
        )


class StrategyPool:
    def __init__(self, size=12):
        self.strategies = [StrategyDNA.random() for _ in range(size)]

    def generate_candidates(self, market_state):
        return self.strategies

    def score(self, candidates, features_by_symbol=None):
        scored = []

        ready_count = 0
        normal_count = 0

        for s in candidates:
            matches = 0
            strength = 0.0

            if features_by_symbol:
                for _sym, f in features_by_symbol.items():
                    if not isinstance(f, dict):
                        continue

                    if not f.get("ready"):
                        continue

                    ready_count += 1

                    # Raw momentum fallback must not be used for scoring/trading.
                    source = str(f.get("source", "NORMAL")).upper()
                    if source == "RAW_MOMENTUM_FALLBACK":
                        continue

                    normal_count += 1

                    trend = float(f.get("trend", 0.0) or 0.0)
                    momentum = float(f.get("momentum", 0.0) or 0.0)
                    signal_strength = float(f.get("signal_strength", 0.0) or 0.0)

                    # Real market-feature match only.
                    if trend > s.trend_threshold and momentum > s.momentum_threshold and signal_strength > 0:
                        matches += 1
                        strength += signal_strength

            if matches == 0:
                base_score = 0.0
            else:
                avg_strength = strength / matches
                base_score = min(0.90, 0.50 + matches * 0.02 + avg_strength * 25)

            scored.append({"strategy": s, "score": base_score, "matches": matches})

        top = sorted(scored, key=lambda x: x["score"], reverse=True)

        if top:
            print(
                "STRATEGY SCORE DEBUG:",
                {
                    "ready_features": ready_count,
                    "normal_features": normal_count,
                    "top_score": round(float(top[0].get("score", 0) or 0), 4),
                    "top_matches": top[0].get("matches", 0),
                    "top_strategy": getattr(top[0].get("strategy"), "name", "unknown"),
                }
            )

        return top

    def evolve(self, scored):
        survivors = [x["strategy"] for x in scored[: max(2, len(scored) // 3)] if x.get("score", 0) > 0]
        if not survivors:
            survivors = [StrategyDNA.random() for _ in range(2)]
        children = []
        for s in survivors:
            children.append(s)
            children.append(s.mutate())
        while len(children) < len(self.strategies):
            children.append(StrategyDNA.random())
        self.strategies = children[: len(self.strategies)]



