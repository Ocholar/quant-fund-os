from prometheus_client import Counter, Gauge, make_asgi_app

trades_total = Counter("quant_trades_total", "Total paper/live trades")
equity_gauge = Gauge("quant_equity", "Current paper portfolio equity")
drawdown_gauge = Gauge("quant_drawdown", "Current drawdown")
metrics_app = make_asgi_app()
