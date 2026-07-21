from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "local"
    live_trading: bool = False
    require_human_approval: bool = True
    database_url: str = "sqlite:////app/data/quant.db"
    redis_url: str = "redis://localhost:6379/0"

    # Capital and risk controls.
    # Keep account-size-sensitive values as percentages/fractions.
    # Do not create fixed-dollar risk assumptions here.
    starting_equity: float = 100.0

    # Drawdown/loss risk.
    max_daily_loss: float = 0.01
    max_portfolio_drawdown: float = 0.05
    caution_drawdown: float = -0.02
    blocked_drawdown: float = -0.05

    # Buffer below hard blocked drawdown where new BUYs are stopped.
    # Example:
    # blocked_drawdown = -0.05
    # near_blocked_drawdown_buffer = 0.0025
    # near_blocked_drawdown = -0.0525
    near_blocked_drawdown_buffer: float = 0.0025

    # Exposure risk.
    max_leverage: float = 0.5
    max_total_exposure_pct: float = 0.08
    max_symbol_exposure_pct: float = 0.03
    caution_exposure_pct: float = 0.06

    # Trade controls.
    max_trades_per_symbol: int = 8
    stop_loss_pct: float = 0.012
    take_profit_pct: float = 0.035
    take_profit_sell_fraction: float = 1.00
    trading_fee_rate: float = 0.0005
    cooldown_seconds: int = 300

    # SIDEWAYS controls.
    sideways_max_entries_per_hour: int = 3
    sideways_min_confidence: float = 0.75
    use_confidence_v2: bool = False

    # Portfolio Manager V2
    pm_v2_enabled: bool = True
    pm_v2_dry_run: bool = True

    # Overnight / small-account risk limits.
    trade_count_window_hours: float = 2

    # Entry Quality Lockdown.
    entry_quality_top_n: int = 2
    # Phase IVB recalibration 2026-07-17: smoothed 20-tick/60-tick MAs compressed
    # the live signal_strength distribution by ~15x vs the old 2-tick/3-tick regime.
    # Old values (0.006 / 0.018) now sit above the theoretical max (~0.0026).
    # New values target P95 (sideways) and P90 (trending) of the live distribution.
    entry_min_signal_sideways: float = 0.0017

    # SIDEWAYS entry pacing / exceptional ladder.
    sideways_entry_min_gap_minutes: float = 15
    sideways_reserve_final_slot_until_minute: int = 35
    # Recalibrated 2026-07-17: old values (0.045-0.070) were above the theoretical
    # max under the smoothed FeatureStore. New values target P99 neighbourhood.
    sideways_exceptional_signal: float = 0.0022
    sideways_exceptional_ladder: str = "0.0022,0.0023,0.0024,0.0025,0.0026"
    sideways_exceptional_bypass_hourly_cap: str = "true"
    sideways_exceptional_bypass_pacing: str = "true"
    entry_require_long_trend: str = "true"

    # Entry cleanup / anti-clustering.
    same_symbol_entry_cooldown_minutes: float = 30
    same_symbol_exceptional_cooldown_minutes: float = 10
    entry_quality_log_rejection_limit: int = 12
    entry_min_signal_trending: float = 0.0015
    entry_max_volatility: float = 0.008
    entry_min_expected_move_pct: float = 0.012
    entry_stop_loss_quarantine_hours: float = 4
    entry_require_triple_agreement: str = "true"

    # Single-exit / breakeven / time-stop.
    full_take_profit_pct: float = 0.012
    breakeven_trigger_pct: float = 0.006
    breakeven_exit_pct: float = 0.001
    position_time_stop_minutes: float = 45
    time_stop_exit_below_pct: float = 0.003

    # TREND controls.
    trending_max_entries_per_hour: int = 48
    trending_min_confidence: float = 0.52

    # Execution controls.
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
    mexc_exchange_type: str = "spot"
    mexc_leverage: int = 1

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    @property
    def near_blocked_drawdown(self) -> float:
        """
        Drawdown threshold where new BUYs should stop before hard BLOCKED.

        Drawdown is negative. Therefore the near-blocked threshold must be
        LESS negative than the hard blocked threshold.

        Example:
            blocked_drawdown = -0.0500
            near_blocked_drawdown_buffer = 0.0025
            near_blocked_drawdown = -0.0475

        Important:
        This must only be applied to CURRENT portfolio state.
        It must not be applied from stale DB snapshots, stale pause state,
        stale state files, or old runtime memory after clean reset.
        """
        return float(self.blocked_drawdown) + abs(float(self.near_blocked_drawdown_buffer))

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

