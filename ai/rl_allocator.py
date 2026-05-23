class SimpleAllocator:
    def allocate(self, scored, market_state):
        if not scored:
            return {"orders": [], "leverage": 0, "estimated_var": 0}
        best = scored[0]
        prices = market_state["prices"]
        features = market_state["features"]
        orders = []
        for symbol, f in features.items():
            if not f.get("ready"):
                continue
            if f["trend"] > best["strategy"].trend_threshold and f["momentum"] > best["strategy"].momentum_threshold:
                notional = market_state["equity"] * best["strategy"].risk_fraction
                qty = notional / f["price"]
                orders.append({"symbol": symbol, "side": "buy", "qty": qty, "price": f["price"], "strategy": best["strategy"].name, "confidence": best["score"], "shadow_mode": best["strategy"].shadow_mode})
        return {"orders": orders, "leverage": 0.25 if orders else 0, "estimated_var": 0.005 if orders else 0}
