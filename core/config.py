from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_env: str = "local"
    live_trading: bool = False
    require_human_approval: bool = True
    database_url: str = "postgresql+psycopg2://quant:quantpass@localhost:5432/quantfund"
    redis_url: str = "redis://localhost:6379/0"
    max_daily_loss: float = 0.01
    max_portfolio_drawdown: float = 0.05
    max_leverage: float = 0.5
    trade_interval_seconds: int = 30
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    exchange_name: str = "paper"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
