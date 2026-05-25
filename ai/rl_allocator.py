class SimpleAllocator:
    def allocate(self, scored, market_state):
        if not scored:
            return {"orders": [], "leverage": 0, "estimated_var": 0}
        best = scored[0]
        prices = market_state["prices"]
        features = market_state["features"]
        orders = []
        # Cash guard: don't allocate more than we have!
        remaining_cash = market_state["equity"]
        
        for symbol, f in features.items():
            if not f.get("ready"):
                continue
            if f["trend"] > best["strategy"].trend_threshold and f["momentum"] > best["strategy"].momentum_threshold:
                notional = market_state["equity"] * best["strategy"].risk_fraction
                
                # Sizing cap
                if notional > remaining_cash:
                    notional = remaining_cash

                if notional < 0.1: # Minimum order size $0.10 (paper-only limit)
                    continue

                qty = notional / f["price"]
                orders.append({"symbol": symbol, "side": "buy", "qty": qty, "price": f["price"], "strategy": best["strategy"].name, "confidence": best["score"], "shadow_mode": best["strategy"].shadow_mode})
                
                if not best["strategy"].shadow_mode:
                    remaining_cash -= notional
                    
        return {"orders": orders, "leverage": (market_state["equity"] - remaining_cash) / market_state["equity"] if market_state["equity"] else 0, "estimated_var": 0.005 if orders else 0}
