from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from core.db import engine
from services.metrics import metrics_app

app = FastAPI(title="Quant Fund OS")
app.mount("/metrics", metrics_app)


def risk_status(exposure_pct, drawdown):
    if drawdown <= -0.05 or exposure_pct >= 0.50:
        return "BLOCKED"
    if drawdown <= -0.02 or exposure_pct >= 0.35:
        return "CAUTION"
    return "SAFE"


def get_positions(conn):
    rows = conn.execute(text("""
        WITH totals AS (
            SELECT
                symbol,
                SUM(CASE WHEN side = 'buy' THEN quantity ELSE 0 END) AS buy_qty,
                SUM(CASE WHEN side = 'sell' THEN quantity ELSE 0 END) AS sell_qty,
                SUM(CASE WHEN side = 'buy' THEN quantity * fill_price ELSE 0 END) AS buy_notional
            FROM trades
            GROUP BY symbol
        ),
        last_prices AS (
            SELECT DISTINCT ON (symbol)
                symbol,
                fill_price AS mark_price
            FROM trades
            ORDER BY symbol, created_at DESC
        )
        SELECT
            t.symbol,
            GREATEST(t.buy_qty - t.sell_qty, 0) AS quantity,
            CASE
                WHEN t.buy_qty > 0 THEN t.buy_notional / t.buy_qty
                ELSE 0
            END AS avg_entry,
            COALESCE(l.mark_price, 0) AS mark_price
        FROM totals t
        LEFT JOIN last_prices l ON t.symbol = l.symbol
        WHERE GREATEST(t.buy_qty - t.sell_qty, 0) > 0
        ORDER BY symbol
    """)).mappings().all()

    positions = []
    for r in rows:
        qty = float(r["quantity"] or 0)
        avg_entry = float(r["avg_entry"] or 0)
        mark = float(r["mark_price"] or 0)
        exposure = qty * mark
        unrealized = qty * (mark - avg_entry)

        positions.append({
            "symbol": r["symbol"],
            "quantity": round(qty, 6),
            "avg_entry": round(avg_entry, 4),
            "mark_price": round(mark, 4),
            "exposure": round(exposure, 2),
            "unrealized_pnl_estimate": round(unrealized, 2),
        })

    return positions


def get_performance(conn, equity):
    counts = conn.execute(text("""
        SELECT
            COUNT(*) AS total_trades,
            COUNT(*) FILTER (WHERE side = 'buy') AS buy_count,
            COUNT(*) FILTER (WHERE side = 'sell') AS sell_count,
            COUNT(*) FILTER (WHERE strategy = 'take_profit') AS take_profit_count,
            COUNT(*) FILTER (WHERE strategy = 'stop_loss') AS stop_loss_count,
            COUNT(*) FILTER (WHERE strategy = 'risk_off_exit') AS risk_off_exit_count,
            COUNT(*) FILTER (WHERE strategy = 'emergency_exposure_reduction') AS emergency_exit_count
        FROM trades
    """)).mappings().first()

    by_symbol = conn.execute(text("""
        SELECT
            symbol,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE side = 'buy') AS buys,
            COUNT(*) FILTER (WHERE side = 'sell') AS sells
        FROM trades
        GROUP BY symbol
        ORDER BY total DESC
        LIMIT 10
    """)).mappings().all()

    sell_count = int(counts["sell_count"] or 0) if counts else 0
    take_profit_count = int(counts["take_profit_count"] or 0) if counts else 0
    stop_loss_count = int(counts["stop_loss_count"] or 0) if counts else 0

    closed_count = take_profit_count + stop_loss_count
    win_rate_estimate = take_profit_count / closed_count if closed_count else 0

    return {
        "total_trades": int(counts["total_trades"] or 0) if counts else 0,
        "buy_count": int(counts["buy_count"] or 0) if counts else 0,
        "sell_count": sell_count,
        "take_profit_count": take_profit_count,
        "stop_loss_count": stop_loss_count,
        "risk_off_exit_count": int(counts["risk_off_exit_count"] or 0) if counts else 0,
        "emergency_exit_count": int(counts["emergency_exit_count"] or 0) if counts else 0,
        "win_rate_estimate": round(win_rate_estimate, 4),
        "realized_pnl_estimate": round(float(equity or 0) - 10000.0, 2),
        "by_symbol": [dict(r) for r in by_symbol],
    }


def get_status_payload():
    with engine.begin() as conn:
        latest = conn.execute(text("""
            SELECT equity, cash, exposure, drawdown, regime, created_at
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

        latest_trades = conn.execute(text("""
            SELECT symbol, side, quantity, fill_price, slippage_bps,
                   strategy, confidence, live, created_at
            FROM trades
            ORDER BY id DESC
            LIMIT 10
        """)).mappings().all()

        portfolio = dict(latest) if latest else {
            "equity": 0,
            "cash": 0,
            "exposure": 0,
            "drawdown": 0,
            "regime": "UNKNOWN",
            "created_at": None,
        }

        equity = float(portfolio.get("equity") or 0)
        cash = float(portfolio.get("cash") or 0)
        exposure = float(portfolio.get("exposure") or 0)
        drawdown = float(portfolio.get("drawdown") or 0)
        exposure_pct = exposure / equity if equity else 0

        positions = get_positions(conn)
        performance = get_performance(conn, equity)

    return {
        "name": "Quant Fund OS",
        "mode": "paper-first",
        "live_trading": False,
        "risk_status": risk_status(exposure_pct, drawdown),
        "portfolio": {
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "exposure": round(exposure, 2),
            "exposure_pct": round(exposure_pct, 4),
            "drawdown": round(drawdown, 4),
            "regime": portfolio.get("regime"),
            "realized_pnl_estimate": performance["realized_pnl_estimate"],
            "updated_at": portfolio.get("created_at"),
        },
        "positions": positions,
        "performance": performance,
        "trading": {
            "total_trades": performance["total_trades"],
            "buy_count": performance["buy_count"],
            "sell_count": performance["sell_count"],
            "latest_trades": [dict(t) for t in latest_trades],
        },
        "risk_rules": {
            "max_total_exposure_pct": 0.50,
            "caution_exposure_pct": 0.35,
            "blocked_drawdown": -0.05,
            "caution_drawdown": -0.02,
        },
    }


@app.get("/")
def root():
    return {
        "name": "Quant Fund OS",
        "mode": "paper-first",
        "status": "running",
        "dashboard": "/dashboard",
        "api": {
            "status": "/status",
            "trades": "/trades",
            "positions": "/positions",
            "metrics": "/metrics-summary",
            "portfolio": "/portfolio/latest",
        },
    }


@app.get("/trades")
def trades(limit: int = 50):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT symbol, side, quantity, fill_price, slippage_bps,
                   strategy, confidence, live, created_at
            FROM trades
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/portfolio")
def portfolio(limit: int = 100):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT equity, cash, exposure, drawdown, regime, created_at
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/portfolio/latest")
def latest_portfolio():
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT equity, cash, exposure, drawdown, regime, created_at
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

    return dict(row) if row else {
        "equity": 0,
        "cash": 0,
        "exposure": 0,
        "drawdown": 0,
        "regime": "UNKNOWN",
    }


@app.get("/positions")
def positions():
    with engine.begin() as conn:
        return get_positions(conn)


@app.get("/metrics-summary")
def metrics_summary():
    with engine.begin() as conn:
        latest = conn.execute(text("""
            SELECT equity
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

        equity = float(latest["equity"] or 0) if latest else 0
        return get_performance(conn, equity)


@app.get("/status")
def status():
    return get_status_payload()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
  <title>Quant Fund OS Dashboard</title>
  <meta http-equiv="refresh" content="10">
  <style>
    body {
      background: #0b0f19;
      color: #e5e7eb;
      font-family: Arial, sans-serif;
      margin: 0;
      padding: 28px;
    }
    h1 { margin-bottom: 4px; font-size: 32px; }
    .subtitle { color: #9ca3af; margin-bottom: 24px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 14px;
      padding: 18px;
      box-shadow: 0 8px 20px rgba(0,0,0,0.25);
      margin-bottom: 24px;
    }
    .label { color: #9ca3af; font-size: 13px; margin-bottom: 8px; }
    .value { font-size: 26px; font-weight: 700; }
    .safe { color: #22c55e; }
    .caution { color: #f59e0b; }
    .blocked { color: #ef4444; }
    .positive { color: #22c55e; }
    .negative { color: #ef4444; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: #111827;
      border-radius: 14px;
      overflow: hidden;
    }
    th, td {
      text-align: left;
      padding: 12px 14px;
      border-bottom: 1px solid #1f2937;
      font-size: 14px;
    }
    th { background: #1f2937; color: #d1d5db; }
    .buy { color: #22c55e; font-weight: 700; }
    .sell { color: #ef4444; font-weight: 700; }
    .footer { margin-top: 18px; color: #6b7280; font-size: 13px; }
    .pill {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #1f2937;
      font-weight: 700;
    }
    h2 { margin-top: 0; }
  </style>
</head>
<body>
  <h1>Quant Fund OS</h1>
  <div class="subtitle">Paper-first autonomous trading dashboard - refreshes every 10 seconds</div>

  <div class="grid">
    <div class="card"><div class="label">Risk Status</div><div id="risk" class="value">Loading...</div></div>
    <div class="card"><div class="label">Equity</div><div id="equity" class="value">-</div></div>
    <div class="card"><div class="label">PnL Estimate</div><div id="pnl" class="value">-</div></div>
    <div class="card"><div class="label">Regime</div><div id="regime" class="value">-</div></div>
  </div>

  <div class="grid">
    <div class="card"><div class="label">Cash</div><div id="cash" class="value">-</div></div>
    <div class="card"><div class="label">Exposure</div><div id="exposure" class="value">-</div></div>
    <div class="card"><div class="label">Exposure %</div><div id="exposurePct" class="value">-</div></div>
    <div class="card"><div class="label">Trades</div><div id="trades" class="value">-</div></div>
  </div>

  <div class="grid">
    <div class="card"><div class="label">Win Rate Estimate</div><div id="winRate" class="value">-</div></div>
    <div class="card"><div class="label">Take Profits</div><div id="takeProfits" class="value positive">-</div></div>
    <div class="card"><div class="label">Stop Losses</div><div id="stopLosses" class="value negative">-</div></div>
    <div class="card"><div class="label">Open Positions</div><div id="openPositions" class="value">-</div></div>
  </div>

  <div class="card">
    <h2>Open Positions</h2>
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Quantity</th>
          <th>Avg Entry</th>
          <th>Mark Price</th>
          <th>Exposure</th>
          <th>Unrealized PnL Estimate</th>
        </tr>
      </thead>
      <tbody id="positionsTable">
        <tr><td colspan="6">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Trades Per Symbol</h2>
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Total</th>
          <th>Buys</th>
          <th>Sells</th>
        </tr>
      </thead>
      <tbody id="symbolTable">
        <tr><td colspan="4">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Latest Trades</h2>
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Fill Price</th>
          <th>Slippage bps</th>
          <th>Strategy</th>
          <th>Confidence</th>
        </tr>
      </thead>
      <tbody id="latestTrades">
        <tr><td colspan="8">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="footer">
    Live trading is OFF. This dashboard is running in paper mode.
  </div>

<script>
async function loadDashboard() {
  const res = await fetch('/status');
  const data = await res.json();

  const p = data.portfolio;
  const t = data.trading;
  const perf = data.performance;
  const positions = data.positions;

  const risk = document.getElementById('risk');
  risk.textContent = data.risk_status;
  risk.className = 'value ' + data.risk_status.toLowerCase();

  document.getElementById('equity').textContent = '$' + p.equity.toFixed(2);

  const pnl = document.getElementById('pnl');
  pnl.textContent = (p.realized_pnl_estimate >= 0 ? '+$' : '-$') + Math.abs(p.realized_pnl_estimate).toFixed(2);
  pnl.className = 'value ' + (p.realized_pnl_estimate >= 0 ? 'positive' : 'negative');

  document.getElementById('regime').textContent = p.regime;
  document.getElementById('cash').textContent = '$' + p.cash.toFixed(2);
  document.getElementById('exposure').textContent = '$' + p.exposure.toFixed(2);
  document.getElementById('exposurePct').textContent = (p.exposure_pct * 100).toFixed(2) + '%';

  document.getElementById('trades').innerHTML =
    '<span class="pill">' + t.total_trades + '</span> ' +
    '<span class="buy">Buy ' + t.buy_count + '</span> / ' +
    '<span class="sell">Sell ' + t.sell_count + '</span>';

  document.getElementById('winRate').textContent = (perf.win_rate_estimate * 100).toFixed(1) + '%';
  document.getElementById('takeProfits').textContent = perf.take_profit_count;
  document.getElementById('stopLosses').textContent = perf.stop_loss_count;
  document.getElementById('openPositions').textContent = positions.length;

  const positionsTable = document.getElementById('positionsTable');
  positionsTable.innerHTML = '';
  if (!positions.length) {
    positionsTable.innerHTML = '<tr><td colspan="6">No open positions</td></tr>';
  } else {
    for (const pos of positions) {
      const pnlClass = pos.unrealized_pnl_estimate >= 0 ? 'positive' : 'negative';
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${pos.symbol}</td>
        <td>${Number(pos.quantity).toFixed(6)}</td>
        <td>${Number(pos.avg_entry).toFixed(4)}</td>
        <td>${Number(pos.mark_price).toFixed(4)}</td>
        <td>$${Number(pos.exposure).toFixed(2)}</td>
        <td class="${pnlClass}">${pos.unrealized_pnl_estimate >= 0 ? '+' : '-'}$${Math.abs(pos.unrealized_pnl_estimate).toFixed(2)}</td>
      `;
      positionsTable.appendChild(row);
    }
  }

  const symbolTable = document.getElementById('symbolTable');
  symbolTable.innerHTML = '';
  if (!perf.by_symbol.length) {
    symbolTable.innerHTML = '<tr><td colspan="4">No symbol data</td></tr>';
  } else {
    for (const s of perf.by_symbol) {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${s.symbol}</td>
        <td>${s.total}</td>
        <td class="buy">${s.buys}</td>
        <td class="sell">${s.sells}</td>
      `;
      symbolTable.appendChild(row);
    }
  }

  const latestTrades = document.getElementById('latestTrades');
  latestTrades.innerHTML = '';
  if (!t.latest_trades.length) {
    latestTrades.innerHTML = '<tr><td colspan="8">No trades yet</td></tr>';
  } else {
    for (const trade of t.latest_trades) {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${trade.created_at}</td>
        <td>${trade.symbol}</td>
        <td class="${trade.side}">${trade.side.toUpperCase()}</td>
        <td>${Number(trade.quantity).toFixed(6)}</td>
        <td>${Number(trade.fill_price).toFixed(4)}</td>
        <td>${Number(trade.slippage_bps).toFixed(2)}</td>
        <td>${trade.strategy}</td>
        <td>${Number(trade.confidence).toFixed(2)}</td>
      `;
      latestTrades.appendChild(row);
    }
  }
}

loadDashboard();
setInterval(loadDashboard, 10000);
</script>
</body>
</html>
    """
