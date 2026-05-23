from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from core.db import engine
from services.metrics import metrics_app
from core.control import is_paused, pause_bot, resume_bot, pause_reason, get_control_state
from services.telegram import send_telegram_alert

app = FastAPI(title="Quant Fund OS")
app.mount("/metrics", metrics_app)


def risk_status(exposure_pct, drawdown):
    if drawdown <= -0.05 or exposure_pct >= 0.50:
        return "BLOCKED"
    if drawdown <= -0.02 or exposure_pct >= 0.35:
        return "CAUTION"
    return "SAFE"


def ensure_positions_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            quantity REAL NOT NULL DEFAULT 0,
            avg_entry REAL NOT NULL DEFAULT 0,
            realized_pnl REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            last_price REAL NOT NULL DEFAULT 0,
            exposure REAL NOT NULL DEFAULT 0,
            strategy TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS symbol_quarantine (
            symbol TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            blocked_until DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))


def get_quarantine(conn):
    rows = conn.execute(text("""
        SELECT symbol, reason, blocked_until, created_at
        FROM symbol_quarantine
        WHERE blocked_until IS NULL OR blocked_until > CURRENT_TIMESTAMP
        ORDER BY created_at DESC
    """)).mappings().all()
    return [
        {
            "symbol": r["symbol"],
            "reason": r["reason"],
            "blocked_until": str(r["blocked_until"]) if r["blocked_until"] else "indefinite",
            "quarantined_at": str(r["created_at"]),
        }
        for r in rows
    ]


def get_positions(conn):
    ensure_positions_table(conn)

    rows = conn.execute(text("""
        SELECT
            symbol,
            quantity,
            avg_entry,
            last_price AS mark_price,
            exposure,
            realized_pnl,
            unrealized_pnl,
            updated_at
        FROM positions
        WHERE quantity > 0.0001
          AND exposure >= 1
        ORDER BY exposure DESC
    """)).mappings().all()

    return [
        {
            "symbol": r["symbol"],
            "quantity": round(float(r["quantity"] or 0), 6),
            "avg_entry": round(float(r["avg_entry"] or 0), 4),
            "mark_price": round(float(r["mark_price"] or 0), 4),
            "exposure": round(float(r["exposure"] or 0), 2),
            "realized_pnl": round(float(r["realized_pnl"] or 0), 2),
            "unrealized_pnl": round(float(r["unrealized_pnl"] or 0), 2),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def get_performance(conn, equity):
    ensure_positions_table(conn)

    summary = conn.execute(text("""
        SELECT
            COUNT(*) AS total_trades,
            SUM(CASE WHEN UPPER(side) = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
            SUM(CASE WHEN UPPER(side) = 'SELL' THEN 1 ELSE 0 END) AS sell_count,
            SUM(CASE WHEN strategy = 'take_profit' THEN 1 ELSE 0 END) AS take_profit_count,
            SUM(CASE WHEN strategy = 'stop_loss' THEN 1 ELSE 0 END) AS stop_loss_count,
            SUM(CASE WHEN strategy = 'risk_off_exit' THEN 1 ELSE 0 END) AS risk_off_exit_count,
            SUM(CASE WHEN strategy = 'emergency_exposure_reduction' THEN 1 ELSE 0 END) AS emergency_exit_count
        FROM trades
    """)).mappings().first()

    pnl_row = conn.execute(text("""
        SELECT
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
            COALESCE(SUM(unrealized_pnl), 0) AS unrealized_pnl
        FROM positions
    """)).mappings().first()

    by_symbol = conn.execute(text("""
        SELECT
            symbol,
            COUNT(*) AS total,
            SUM(CASE WHEN UPPER(side) = 'BUY' THEN 1 ELSE 0 END) AS buys,
            SUM(CASE WHEN UPPER(side) = 'SELL' THEN 1 ELSE 0 END) AS sells,
            CAST(COALESCE(SUM(pnl), 0) AS NUMERIC) AS realized_pnl
        FROM trades
        GROUP BY symbol
        ORDER BY total DESC
        LIMIT 10
    """)).mappings().all()

    sell_count = int(summary["sell_count"] or 0) if summary else 0
    take_profit_count = int(summary["take_profit_count"] or 0) if summary else 0
    stop_loss_count = int(summary["stop_loss_count"] or 0) if summary else 0

    closed_count = take_profit_count + stop_loss_count
    win_rate_estimate = take_profit_count / closed_count if closed_count else 0

    realized_pnl = float(pnl_row["realized_pnl"] or 0) if pnl_row else 0.0
    unrealized_pnl = float(pnl_row["unrealized_pnl"] or 0) if pnl_row else 0.0

    return {
        "total_trades": int(summary["total_trades"] or 0) if summary else 0,
        "buy_count": int(summary["buy_count"] or 0) if summary else 0,
        "sell_count": int(summary["sell_count"] or 0) if summary else 0,
        "win_rate": round(win_rate_estimate, 4),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": round(realized_pnl + unrealized_pnl, 2),
        "by_symbol": [
            {
                "symbol": r["symbol"],
                "total": int(r["total"] or 0),
                "buys": int(r["buys"] or 0),
                "sells": int(r["sells"] or 0),
                "realized_pnl": float(r["realized_pnl"] or 0)
            }
            for r in by_symbol
        ],
    }


def get_status_payload():
    with engine.begin() as conn:
        ensure_positions_table(conn)

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
            "equity": 100,
            "cash": 100,
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
        quarantine = get_quarantine(conn)

        scores = conn.execute(text("""
            SELECT strategy, score, status
            FROM strategy_scores
            ORDER BY score DESC
        """)).mappings().all()

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
            "realized_pnl": performance["realized_pnl"],
            "unrealized_pnl": performance["unrealized_pnl"],
            "total_pnl": performance["total_pnl"],
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
        "quarantine": quarantine,
        "strategy_scores": [dict(s) for s in scores],
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
        "equity": 100,
        "cash": 100,
        "exposure": 0,
        "drawdown": 0,
        "regime": "UNKNOWN",
    }


@app.get("/positions")
def positions():
    with engine.begin() as conn:
        return get_positions(conn)


@app.get("/quarantine")
def quarantine():
    with engine.begin() as conn:
        ensure_positions_table(conn)
        return get_quarantine(conn)


@app.delete("/quarantine/{symbol}")
def release_quarantine(symbol: str):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM symbol_quarantine WHERE symbol = :sym"), {"sym": symbol})
    return {"status": "released", "symbol": symbol}


@app.get("/strategy-scores")
def strategy_scores():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT strategy, sharpe, drawdown, score, status, created_at
            FROM strategy_scores
            ORDER BY score DESC
        """)).mappings().all()
    return [dict(r) for r in rows]


@app.delete("/strategy-scores/{strategy}")
def reset_strategy(strategy: str):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM strategy_scores WHERE strategy = :s"), {"s": strategy})
    return {"status": "reset", "strategy": strategy}


@app.get("/metrics-summary")
def metrics_summary():
    with engine.begin() as conn:
        latest = conn.execute(text("""
            SELECT equity
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

        equity = float(latest["equity"] or 0) if latest else 100
        return get_performance(conn, equity)


@app.get("/status")
def status():
    payload = get_status_payload()
    control = get_control_state()
    paused = control["paused"]

    payload["paused"] = paused
    payload["pause_reason"] = control.get("reason") or ""
    payload["bot_state"] = "PAUSED" if paused else "RUNNING"
    payload["controls"] = {
        "pause": "/pause",
        "resume": "/resume",
        "kill_switch": "/kill-switch",
    }
    return payload


@app.post("/pause")
def pause():
    pause_bot("manual_pause")
    send_telegram_alert(
        "<b>Quant Fund OS PAUSED</b>\n"
        "New positions are blocked.\n"
        "Existing monitoring remains active."
    )
    return {"status": "paused", "paused": True}


@app.post("/resume")
def resume():
    resume_bot()
    send_telegram_alert(
        "<b>Quant Fund OS RESUMED</b>\n"
        "Paper trading engine is active again.\n"
        "Live trading remains OFF."
    )
    return {"status": "running", "paused": False}


@app.post("/kill-switch")
def kill_switch():
    pause_bot("manual_kill_switch")
    send_telegram_alert(
        "<b>EMERGENCY KILL SWITCH ACTIVATED</b>\n"
        "New positions are blocked immediately.\n"
        "Live trading remains OFF."
    )
    return {
        "status": "kill_switch_active",
        "paused": True,
        "message": "Bot will not open new positions while kill switch is active.",
    }


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
    .state-banner {
      margin-bottom: 24px;
      padding: 14px 18px;
      border-radius: 14px;
      font-size: 20px;
      font-weight: 800;
      border: 1px solid #1f2937;
      background: #111827;
    }
    .state-running {
      color: #22c55e;
      border-color: #14532d;
    }
    .state-paused {
      color: #f59e0b;
      border-color: #92400e;
    }
    .controls {
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
    }
    button {
      border: none;
      border-radius: 12px;
      padding: 12px 18px;
      font-size: 15px;
      font-weight: 800;
      cursor: pointer;
    }
    .btn-resume {
      background: #16a34a;
      color: white;
    }
    .btn-pause {
      background: #f59e0b;
      color: #111827;
    }
    .btn-kill {
      background: #dc2626;
      color: white;
    }
    button:hover { opacity: 0.88; }
  </style>
</head>
<body>
  <h1>Quant Fund OS</h1>
  <div class="subtitle">Paper-first autonomous trading dashboard - refreshes every 10 seconds</div>
  <div id="botStateBanner" class="state-banner">Loading bot state...</div>

  <div class="controls">
    <button class="btn-resume" onclick="controlBot('resume')">Resume</button>
    <button class="btn-pause" onclick="controlBot('pause')">Pause</button>
    <button class="btn-kill" onclick="controlBot('kill-switch')">Kill Switch</button>
  </div>

  <div class="grid">
    <div class="card"><div class="label">Risk Status</div><div id="risk" class="value">Loading...</div></div>
    <div class="card"><div class="label">Equity</div><div id="equity" class="value">-</div></div>
    <div class="card"><div class="label">Total Real PnL</div><div id="pnl" class="value">-</div></div>
    <div class="card"><div class="label">Regime</div><div id="regime" class="value">-</div></div>
  </div>

  <div class="grid">
    <div class="card"><div class="label">Cash</div><div id="cash" class="value">-</div></div>
    <div class="card"><div class="label">Exposure</div><div id="exposure" class="value">-</div></div>
    <div class="card"><div class="label">Exposure %</div><div id="exposurePct" class="value">-</div></div>
    <div class="card"><div class="label">Trades</div><div id="trades" class="value">-</div></div>
  </div>

  <div class="grid">
    <div class="card"><div class="label">Realized PnL</div><div id="realizedPnl" class="value">-</div></div>
    <div class="card"><div class="label">Unrealized PnL</div><div id="unrealizedPnl" class="value">-</div></div>
    <div class="card"><div class="label">Win Rate Estimate</div><div id="winRate" class="value">-</div></div>
    <div class="card"><div class="label">Open Positions</div><div id="openPositions" class="value">-</div></div>
  </div>

  <div class="grid">
    <div class="card"><div class="label">Take Profits</div><div id="takeProfits" class="value positive">-</div></div>
    <div class="card"><div class="label">Stop Losses</div><div id="stopLosses" class="value negative">-</div></div>
    <div class="card"><div class="label">Risk-Off Exits</div><div id="riskOffExits" class="value">-</div></div>
    <div class="card"><div class="label">Emergency Exits</div><div id="emergencyExits" class="value">-</div></div>
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
          <th>Realized PnL</th>
          <th>Unrealized PnL</th>
        </tr>
      </thead>
      <tbody id="positionsTable">
        <tr><td colspan="7">Loading...</td></tr>
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
          <th>Realized PnL</th>
        </tr>
      </thead>
      <tbody id="symbolTable">
        <tr><td colspan="5">Loading...</td></tr>
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

  <div class="card">
    <h2>Strategy Performance</h2>
    <table>
      <thead>
        <tr>
          <th>Strategy</th>
          <th>Score</th>
          <th>Status</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="strategyTable">
        <tr><td colspan="4">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Symbol Quarantine</h2>
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Reason</th>
          <th>Blocked Until</th>
          <th>Quarantined At</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="quarantineTable">
        <tr><td colspan="5">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="footer">
    Live trading is OFF. This dashboard is running in paper mode.
  </div>

<script>
function money(value) {
  const n = Number(value ?? 0);
  const sign = n >= 0 ? '+$' : '-$';
  return sign + Math.abs(n).toFixed(2);
}

function dollars(value) {
  return '$' + Number(value ?? 0).toFixed(2);
}

function num(value, digits = 2) {
  return Number(value ?? 0).toFixed(digits);
}

function pnlClass(value) {
  return Number(value ?? 0) >= 0 ? 'positive' : 'negative';
}

async function controlBot(action) {
  const confirmed = action === 'kill-switch'
    ? confirm('Activate emergency kill switch? New positions will be blocked.')
    : true;

  if (!confirmed) return;

  const res = await fetch('/' + action, { method: 'POST' });

  if (!res.ok) {
    alert('Control action failed: ' + action);
    return;
  }

  await loadDashboard();
}

async function loadDashboard() {
  try {
    const res = await fetch('/status');
    const data = await res.json();

    const p = data.portfolio ?? {};
    const t = data.trading ?? {};
    const perf = data.performance ?? {};
    const positions = data.positions ?? [];

    const banner = document.getElementById('botStateBanner');
    if (data.paused) {
     const reason = data.pause_reason || 'paused';
     const isAuto = reason.includes('max_daily_loss') || reason.includes('liquidity_error');
     banner.textContent = (isAuto ? 'AUTO-PAUSED' : 'PAUSED') + ' - ' + reason + '. New positions are blocked.';
     banner.className = 'state-banner state-paused';
    } else {
     banner.textContent = 'RUNNING - Paper trading active. Live trading OFF.';
     banner.className = 'state-banner state-running';
    }

    const risk = document.getElementById('risk');
    risk.textContent = data.risk_status ?? 'UNKNOWN';
    risk.className = 'value ' + String(data.risk_status ?? 'safe').toLowerCase();

    document.getElementById('equity').textContent = dollars(p.equity);
    document.getElementById('regime').textContent = p.regime ?? 'UNKNOWN';
    document.getElementById('cash').textContent = dollars(p.cash);
    document.getElementById('exposure').textContent = dollars(p.exposure);
    document.getElementById('exposurePct').textContent = (Number(p.exposure_pct ?? 0) * 100).toFixed(2) + '%';

    const pnl = document.getElementById('pnl');
    pnl.textContent = money(p.total_pnl);
    pnl.className = 'value ' + pnlClass(p.total_pnl);

    const realizedPnl = document.getElementById('realizedPnl');
    realizedPnl.textContent = money(p.realized_pnl);
    realizedPnl.className = 'value ' + pnlClass(p.realized_pnl);

    const unrealizedPnl = document.getElementById('unrealizedPnl');
    unrealizedPnl.textContent = money(p.unrealized_pnl);
    unrealizedPnl.className = 'value ' + pnlClass(p.unrealized_pnl);

    document.getElementById('trades').innerHTML =
      '<span class="pill">' + Number(t.total_trades ?? 0) + '</span> ' +
      '<span class="buy">Buy ' + Number(t.buy_count ?? 0) + '</span> / ' +
      '<span class="sell">Sell ' + Number(t.sell_count ?? 0) + '</span>';

    document.getElementById('winRate').textContent = (Number(perf.win_rate_estimate ?? 0) * 100).toFixed(1) + '%';
    document.getElementById('takeProfits').textContent = Number(perf.take_profit_count ?? 0);
    document.getElementById('stopLosses').textContent = Number(perf.stop_loss_count ?? 0);
    document.getElementById('riskOffExits').textContent = Number(perf.risk_off_exit_count ?? 0);
    document.getElementById('emergencyExits').textContent = Number(perf.emergency_exit_count ?? 0);
    document.getElementById('openPositions').textContent = positions.length;

    const positionsTable = document.getElementById('positionsTable');
    positionsTable.innerHTML = '';

    if (!positions.length) {
      positionsTable.innerHTML = '<tr><td colspan="7">No open positions</td></tr>';
    } else {
      for (const pos of positions) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${pos.symbol}</td>
          <td>${num(pos.quantity, 6)}</td>
          <td>${num(pos.avg_entry, 4)}</td>
          <td>${num(pos.mark_price, 4)}</td>
          <td>${dollars(pos.exposure)}</td>
          <td class="${pnlClass(pos.realized_pnl)}">${money(pos.realized_pnl)}</td>
          <td class="${pnlClass(pos.unrealized_pnl)}">${money(pos.unrealized_pnl)}</td>
        `;
        positionsTable.appendChild(row);
      }
    }

    const symbolTable = document.getElementById('symbolTable');
    symbolTable.innerHTML = '';

    if (!Array.isArray(perf.by_symbol) || !perf.by_symbol.length) {
      symbolTable.innerHTML = '<tr><td colspan="5">No symbol data</td></tr>';
    } else {
      for (const s of perf.by_symbol) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${s.symbol}</td>
          <td>${Number(s.total ?? 0)}</td>
          <td class="buy">${Number(s.buys ?? 0)}</td>
          <td class="sell">${Number(s.sells ?? 0)}</td>
          <td class="${pnlClass(s.realized_pnl)}">${money(s.realized_pnl)}</td>
        `;
        symbolTable.appendChild(row);
      }
    }

    const latestTrades = document.getElementById('latestTrades');
    latestTrades.innerHTML = '';

    const latest = t.latest_trades ?? [];
    if (!latest.length) {
      latestTrades.innerHTML = '<tr><td colspan="8">No trades yet</td></tr>';
    } else {
      for (const trade of latest) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${trade.created_at}</td>
          <td>${trade.symbol}</td>
          <td class="${trade.side}">${String(trade.side).toUpperCase()}</td>
          <td>${num(trade.quantity, 6)}</td>
          <td>${num(trade.fill_price, 4)}</td>
          <td>${num(trade.slippage_bps, 2)}</td>
          <td>${trade.strategy}</td>
          <td>${num(trade.confidence, 2)}</td>
        `;
        latestTrades.appendChild(row);
      }
    }

    const quarantineTable = document.getElementById('quarantineTable');
    quarantineTable.innerHTML = '';
    const quarantine = data.quarantine ?? [];
    if (!quarantine.length) {
      quarantineTable.innerHTML = '<tr><td colspan="5" style="color:#22c55e">No symbols quarantined ✓</td></tr>';
    } else {
      for (const q of quarantine) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td style="color:#ef4444;font-weight:700">${q.symbol}</td>
          <td>${q.reason}</td>
          <td>${q.blocked_until}</td>
          <td>${q.quarantined_at}</td>
          <td><button onclick="releaseSymbol('${q.symbol}')" style="padding:4px 8px;font-size:12px;background:#374151;color:#fff;border:none;border-radius:6px;cursor:pointer">Release</button></td>
        `;
        quarantineTable.appendChild(row);
      }
    }

    const strategyTable = document.getElementById('strategyTable');
    strategyTable.innerHTML = '';
    const scores = data.strategy_scores ?? [];
    if (!scores.length) {
      strategyTable.innerHTML = '<tr><td colspan="4">No strategy scores yet</td></tr>';
    } else {
      for (const s of scores) {
        const row = document.createElement('tr');
        const isBlocked = s.status === 'blocked';
        row.innerHTML = `
          <td>${s.strategy}</td>
          <td class="${pnlClass(s.score)}">${money(s.score)}</td>
          <td class="${isBlocked ? 'blocked' : 'safe'}">${s.status}</td>
          <td><button onclick="resetStrategy('${s.strategy}')" style="padding:4px 8px;font-size:12px;background:#374151;color:#fff;border:none;border-radius:6px;cursor:pointer">Reset</button></td>
        `;
        strategyTable.appendChild(row);
      }
    }
  } catch (err) {
    console.error('Dashboard load failed:', err);
  }
}

async function releaseSymbol(symbol) {
  if (!confirm('Release ' + symbol + ' from quarantine?')) return;
  await fetch('/quarantine/' + encodeURIComponent(symbol), { method: 'DELETE' });
  await loadDashboard();
}

async function resetStrategy(strategy) {
  if (!confirm('Reset scores even if blocked for ' + strategy + '?')) return;
  await fetch('/strategy-scores/' + encodeURIComponent(strategy), { method: 'DELETE' });
  await loadDashboard();
}

loadDashboard();
setInterval(loadDashboard, 10000);
</script>
</body>
</html>
    """
