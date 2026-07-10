def qfo_safe_lower(v):
    if not isinstance(v, str):
        return ""
    return v.lower()

def qfo_trade_get(trade, key, default=None):
    if isinstance(trade, dict):
        return trade.get(key, default)
    if hasattr(trade, key):
        return getattr(trade, key, default)
    return default

def qfo_trade_label(trade):
    value = qfo_trade_get(trade, "strategy")
    if not value:
        value = qfo_trade_get(trade, "reason")
    parts = []
    if isinstance(value, str):
        parts.append(qfo_safe_lower(value))
    elif isinstance(value, list):
        for part in value:
            if isinstance(part, str):
                parts.append(qfo_safe_lower(part))
    elif isinstance(value, dict):
        value = value.get("name") or value.get("label") or value.get("type")
        if value:
            parts.append(qfo_safe_lower(value))
    return " ".join(parts)

def qfo_trade_side(trade):
    return qfo_safe_lower(qfo_trade_get(trade, "side", ""))

def qfo_is_buy_trade(trade):
    return qfo_trade_side(trade) == "buy"

def qfo_is_sell_trade(trade):
    return qfo_trade_side(trade) == "sell"

def qfo_is_stop_loss_label(label):
    label = qfo_safe_lower(label)

    if "stop_loss" in label:
        return True

    aliases = (
        " stop loss ",
        " stopped out ",
        " stopped_out ",
        " initial sl ",
        " hard_sl ",
        " hard sl ",
    )
    padded = f" {label} "
    return any(alias in padded for alias in aliases)

def qfo_is_take_profit_label(label):
    label = qfo_safe_lower(label)

    if qfo_is_stop_loss_label(label):
        return False

    profit_markers = (
        "take_profit",
        "take profit",
        "adaptive_take_profit",
        "trailing_profit_exit",
        "profit_exit",
        "scalp_take_profit",
        "tp_exit",
    )
    return any(marker in label for marker in profit_markers)

def qfo_is_stop_loss_trade(trade):
    return qfo_is_sell_trade(trade) and qfo_is_stop_loss_label(qfo_trade_label(trade))

def qfo_is_take_profit_trade(trade):
    return qfo_is_sell_trade(trade) and qfo_is_take_profit_label(qfo_trade_label(trade))

def qfo_compute_truth_metrics_from_trades(trades):
    buy_count = 0
    sell_count = 0
    take_profit_count = 0
    stop_loss_count = 0
    breakeven_protection_exit_count = 0
    time_stop_exit_count = 0

    for trade in trades:
        if qfo_is_buy_trade(trade):
            buy_count += 1
        elif qfo_is_sell_trade(trade):
            sell_count += 1

        if qfo_is_take_profit_trade(trade):
            take_profit_count += 1

        if qfo_is_stop_loss_trade(trade):
            stop_loss_count += 1
            
        label = qfo_safe_lower(qfo_trade_label(trade))
        if label == "breakeven_protection_exit":
            breakeven_protection_exit_count += 1
        elif label == "time_stop_exit":
            time_stop_exit_count += 1

    completed_classified = take_profit_count + stop_loss_count

    if completed_classified > 0:
        win_rate = take_profit_count / completed_classified
    else:
        win_rate = 0.0

    return {
        "total_trades": buy_count + sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "take_profit_count": take_profit_count,
        "stop_loss_count": stop_loss_count,
        "breakeven_protection_exit_count": breakeven_protection_exit_count,
        "time_stop_exit_count": time_stop_exit_count,
        "win_rate": win_rate,
        "win_rate_estimate": win_rate,
    }
