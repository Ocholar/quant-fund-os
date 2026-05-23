import ccxt
import logging
from core.config import settings
from execution.order_book import OrderBook
from execution.slippage import slippage_bps

logger = logging.getLogger(__name__)

class PaperExecutor:
    def execute(self, symbol: str, side: str, qty: float, expected_price: float) -> dict:
        book = OrderBook.synthetic(expected_price)
        try:
            fill = book.market_buy(qty) if side == "buy" else book.market_sell(qty)
        except ValueError as e:
            if "insufficient synthetic liquidity" in str(e).lower():
                raise ValueError(f"insufficient synthetic liquidity for {symbol}") from e
            raise
        return {
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "expected_price": expected_price,
            "fill_price": fill,
            "slippage_bps": slippage_bps(expected_price, fill, side),
        }

class RealMEXCExecutor:
    def __init__(self):
        self.active = settings.live_trading and settings.mexc_api_key
        if self.active:
            self.exchange = ccxt.mexc({
                'apiKey': settings.mexc_api_key,
                'secret': settings.mexc_api_secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot'
                }
            })
            logger.info("RealMEXCExecutor initialized for live trading.")
        else:
            self.exchange = None
            logger.info("RealMEXCExecutor in standby (Paper Mode).")

    def execute(self, symbol: str, side: str, qty: float, expected_price: float) -> dict:
        if not self.active:
            raise RuntimeError("RealMEXCExecutor called while not active (LIVE_TRADING=false)")

        try:
            # MEXC market orders
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=side,
                amount=qty
            )
            
            fill_price = float(order.get('price') or order.get('average') or expected_price)
            
            return {
                "symbol": symbol,
                "side": side,
                "quantity": qty,
                "expected_price": expected_price,
                "fill_price": fill_price,
                "slippage_bps": 0.0, 
                "mexc_order_id": order.get('id')
            }
        except Exception as e:
            logger.error(f"MEXC Execution Error: {e}")
            raise
