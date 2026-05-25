from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_env: str = "local"
    live_trading: bool = False
    require_human_approval: bool = True
    database_url: str = "sqlite:///quant.db"
    redis_url: str = "redis://localhost:6379/0"
    max_daily_loss: float = 0.01
    max_portfolio_drawdown: float = 0.05
    max_leverage: float = 0.5
    trade_interval_seconds: int = 30
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    exchange_name: str = "paper"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    mexc_api_key: str = ""
    mexc_api_secret: str = ""
    mexc_exchange_type: str = "spot" # "spot" or "swap"
    mexc_leverage: int = 1         # 1x for spot, up to 100x for futures

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
