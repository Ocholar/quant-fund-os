from ai.evolutionary_engine import StrategyPool
from ai.rl_allocator import SimpleAllocator
from ai.online_learning import OnlineLearner

class AutonomousFundAgent:
    def __init__(self, risk_engine, executor):
        self.strategy_pool = StrategyPool()
        self.allocator = SimpleAllocator()
        self.learner = OnlineLearner()
        self.risk_engine = risk_engine
        self.executor = executor
        self.cycles = 0

    def run_cycle(self, market_state):
        candidates = self.strategy_pool.generate_candidates(market_state)
        scored = self.strategy_pool.score(candidates, market_state.get("features"))
        allocation = self.allocator.allocate(scored, market_state)
        approved = self.risk_engine.approve(allocation)
        if approved is None:
            return {"status": "blocked_by_risk", "orders": []}
        fills = []
        for order in approved["orders"]:
            fills.append(self.executor.execute(order["symbol"], order["side"], order["qty"], order["price"]) | {
                "strategy": order["strategy"],
                "confidence": order["confidence"],
                "shadow_mode": order.get("shadow_mode", False)
            })
        self.cycles += 1
        if self.cycles % 50 == 0:
            self.strategy_pool.evolve(scored)
        return {"status": "ok", "orders": fills, "best_score": scored[0]["score"] if scored else 0}
