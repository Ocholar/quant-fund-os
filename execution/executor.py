from execution.order_book import OrderBook
from execution.slippage import slippage_bps

class PaperExecutor:
    def execute(self, symbol: str, side: str, qty: float, expected_price: float) -> dict:
        book = OrderBook.synthetic(expected_price)
        fill = book.market_buy(qty) if side == "buy" else book.market_sell(qty)
        return {
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "expected_price": expected_price,
            "fill_price": fill,
            "slippage_bps": slippage_bps(expected_price, fill, side),
        }
