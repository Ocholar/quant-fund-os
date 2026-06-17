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
        blocked_sources = {"FALLBACK_SCOUT_BREAKOUT", "RAW_MOMENTUM_FALLBACK", "RAW_MOMENTUM"}
        for order in approved["orders"]:
            side = str(order.get("side", "")).lower()
            feature_source = str(order.get("feature_source") or order.get("feature", {}).get("source", "")).upper()
            strategy_name = str(order.get("strategy", ""))

            if side == "buy":
                if feature_source != "NORMAL":
                    print(
                        f"AGENT FINAL BUY BLOCK: source_not_normal "
                        f"symbol={order.get('symbol')} source={feature_source or 'missing'} strategy={strategy_name}"
                    )
                    continue

                if (
                    strategy_name.upper() in blocked_sources
                    or feature_source in blocked_sources
                    or "FALLBACK" in strategy_name.upper()
                    or "RAW_MOMENTUM" in strategy_name.upper()
                ):
                    print(
                        f"AGENT FINAL BUY BLOCK: fallback_source_disabled "
                        f"symbol={order.get('symbol')} source={feature_source} strategy={strategy_name}"
                    )
                    continue

            fills.append(self.executor.execute(order["symbol"], order["side"], order["qty"], order["price"]) | {
                "strategy": order["strategy"],
                "confidence": order["confidence"],
                "shadow_mode": order.get("shadow_mode", False),
                "feature_source": order.get("feature_source"),
                "signal_strength": order.get("signal_strength"),
                "symbol_regime": order.get("symbol_regime"),
                "entry_reason": order.get("entry_reason"),
            })
        self.cycles += 1
        if self.cycles % 50 == 0:
            self.strategy_pool.evolve(scored)
        return {"status": "ok", "orders": fills, "best_score": scored[0]["score"] if scored else 0}
