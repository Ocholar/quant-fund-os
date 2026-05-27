from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "local"
    live_trading: bool = False
    require_human_approval: bool = True
    database_url: str = "sqlite:///quant.db"
    redis_url: str = "redis://localhost:6379/0"

    # Capital and risk controls. Keep these in .env so the bot does not
    # silently mix $10, $100, and $10,000 assumptions across modules.
    starting_equity: float = 100.0
    max_daily_loss: float = 0.01
    max_portfolio_drawdown: float = 0.05
    max_leverage: float = 0.5
    max_total_exposure_pct: float = 0.08
    max_symbol_exposure_pct: float = 0.03
    max_trades_per_symbol: int = 8
    stop_loss_pct: float = 0.012
    take_profit_pct: float = 0.035
    take_profit_sell_fraction: float = 1.00
    trading_fee_rate: float = 0.0005
    cooldown_seconds: int = 300
    sideways_max_entries_per_hour: int = 3
    sideways_min_confidence: float = 0.75

    # Overnight / small-account risk limits
    trade_count_window_hours: float = 2
    caution_exposure_pct: float = 0.06

    # Entry Quality Lockdown
    entry_quality_top_n: int = 2
    entry_min_signal_sideways: float = 0.025

    # SIDEWAYS entry pacing / exceptional ladder
    sideways_entry_min_gap_minutes: float = 15
    sideways_reserve_final_slot_until_minute: int = 35
    sideways_exceptional_signal: float = 0.045
    sideways_exceptional_ladder: str = "0.045,0.050,0.055,0.060,0.065,0.070"
    sideways_exceptional_bypass_hourly_cap: str = "true"
    sideways_exceptional_bypass_pacing: str = "true"
    entry_require_long_trend: str = "true"

    # Entry cleanup / anti-clustering
    same_symbol_entry_cooldown_minutes: float = 30
    same_symbol_exceptional_cooldown_minutes: float = 10
    entry_quality_log_rejection_limit: int = 12
    entry_min_signal_trending: float = 0.018
    entry_max_volatility: float = 0.008
    entry_min_expected_move_pct: float = 0.012
    entry_stop_loss_quarantine_hours: float = 4
    entry_require_triple_agreement: str = "true"

    # Single-exit / breakeven / time-stop
    full_take_profit_pct: float = 0.012
    breakeven_trigger_pct: float = 0.006
    breakeven_exit_pct: float = 0.001
    position_time_stop_minutes: float = 45
    time_stop_exit_below_pct: float = 0.003
    trending_max_entries_per_hour: int = 48
    trending_min_confidence: float = 0.52
    max_new_entries_per_cycle: int = 2
    min_trade_notional: float = 0.05
    stablecoin_filter_enabled: bool = True

    trade_interval_seconds: int = 30
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    exchange_name: str = "paper"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    mexc_api_key: str = ""
    mexc_api_secret: str = ""
    mexc_exchange_type: str = "spot"  # "spot" or "swap"
    mexc_leverage: int = 1             # 1x for spot; futures support still needs more guards

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()



















