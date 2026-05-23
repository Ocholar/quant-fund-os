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
            updated_at DATETIME DEFAULT (DATETIME('now', '+3 hours'))
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS symbol_quarantine (
            symbol TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            blocked_until DATETIME,
            created_at DATETIME DEFAULT (DATETIME('now', '+3 hours'))
        )
    """))


def get_quarantine(conn):
    rows = conn.execute(text("""
        SELECT symbol, reason, blocked_until, created_at
        FROM symbol_quarantine
        WHERE blocked_until IS NULL OR blocked_until > DATETIME('now', '+3 hours')
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
        "take_profit_count": take_profit_count,
        "stop_loss_count": stop_loss_count,
        "risk_off_exit_count": int(summary["risk_off_exit_count"] or 0) if summary else 0,
        "emergency_exit_count": int(summary["emergency_exit_count"] or 0) if summary else 0,
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
        "Timezone: Kenyan Time (GMT+3)\n"
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
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Quant Fund OS | Institutional Intelligence</title>
  <meta http-equiv="refresh" content="10">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #05070a;
      --card-bg: rgba(17, 24, 39, 0.7);
      --border: rgba(255, 255, 255, 0.08);
      --text-main: #f9fafb;
      --text-dim: #9ca3af;
      --accent: #6366f1;
      --accent-glow: rgba(99, 102, 241, 0.3);
      --success: #10b981;
      --error: #f43f5e;
      --warning: #f59e0b;
    }

    * { box-sizing: border-box; }

    body {
      background: var(--bg);
      background-image: 
        radial-gradient(circle at 0% 0%, rgba(99, 102, 241, 0.15) 0%, transparent 40%),
        radial-gradient(circle at 100% 100%, rgba(16, 185, 129, 0.1) 0%, transparent 40%);
      color: var(--text-main);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      margin: 0;
      padding: 40px;
      line-height: 1.5;
      min-height: 100vh;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-bottom: 32px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 24px;
    }

    h1 {
      margin: 0;
      font-size: 38px;
      font-weight: 800;
      letter-spacing: -1px;
      background: linear-gradient(135deg, #fff 0%, #a5b4fc 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .subtitle {
      color: var(--text-dim);
      font-size: 14px;
      margin-top: 4px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 1px;
    }

    .state-badge {
      padding: 8px 16px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
      backdrop-filter: blur(8px);
      border: 1px solid var(--border);
      transition: all 0.3s ease;
    }

    .pulse {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
      box-shadow: 0 0 12px currentColor;
      animation: pulse-kf 2s infinite;
    }

    @keyframes pulse-kf {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.2); }
      100% { opacity: 1; transform: scale(1); }
    }

    .state-running { background: rgba(16, 185, 129, 0.1); color: var(--success); border-color: rgba(16, 185, 129, 0.2); }
    .state-paused { background: rgba(245, 158, 11, 0.1); color: var(--warning); border-color: rgba(245, 158, 11, 0.2); }

    .controls {
      display: flex;
      gap: 12px;
      margin-bottom: 32px;
    }

    button {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 20px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      background: var(--card-bg);
      color: var(--text-main);
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      backdrop-filter: blur(12px);
    }

    button:hover { background: rgba(255, 255, 255, 0.05); transform: translateY(-1px); border-color: var(--accent); }
    .btn-resume { border-color: var(--success); color: var(--success); }
    .btn-kill { border-color: var(--error); color: var(--error); }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 20px;
      margin-bottom: 40px;
    }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 24px;
      backdrop-filter: blur(16px);
      transition: box-shadow 0.3s ease;
    }

    .card:hover { box-shadow: 0 10px 40px -10px rgba(0,0,0,0.5); }

    .label {
      color: var(--text-dim);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      margin-bottom: 12px;
      display: flex;
      justify-content: space-between;
    }

    .value {
      font-size: 32px;
      font-weight: 800;
      font-family: 'JetBrains Mono', monospace;
      letter-spacing: -1px;
    }

    .pnl-chip {
      font-size: 13px;
      padding: 2px 8px;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.05);
      margin-top: 8px;
      display: inline-block;
    }

    .positive { color: var(--success); }
    .negative { color: var(--error); }

    .section-title {
      font-size: 20px;
      font-weight: 700;
      margin: 0 0 20px 0;
      display: flex;
      align-items: center;
      gap: 12px;
    }

    table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      font-size: 14px;
    }

    th {
      text-align: left;
      padding: 12px 16px;
      color: var(--text-dim);
      font-weight: 600;
      border-bottom: 1px solid var(--border);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }

    td {
      padding: 16px;
      border-bottom: 1px solid var(--border);
      font-family: 'JetBrains Mono', monospace;
    }

    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }

    .symbol-pill {
      background: rgba(99, 102, 241, 0.1);
      color: #a5b4fc;
      padding: 4px 10px;
      border-radius: 8px;
      font-weight: 700;
      border: 1px solid rgba(99, 102, 241, 0.2);
    }

    .strategy-pill {
      font-size: 12px;
      background: #1f2937;
      padding: 2px 8px;
      border-radius: 6px;
      color: var(--text-dim);
    }

    .footer {
      margin-top: 60px;
      text-align: center;
      color: var(--text-dim);
      font-size: 13px;
      opacity: 0.5;
    }

    @media (max-width: 768px) {
      body { padding: 20px; }
      header { flex-direction: column; align-items: flex-start; gap: 20px; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Quant Fund OS</h1>
      <div class="subtitle">Institutional Grade Autonomous Research v2.5</div>
    </div>
    <div id="botStateBadge" class="state-badge">
       <div class="pulse"></div>
       <span id="stateText">Initializing Hub...</span>
    </div>
  </header>

  <div class="controls">
    <button class="btn-resume" onclick="controlBot('resume')">Deploy Fleet</button>
    <button class="btn-pause" onclick="controlBot('pause')">Secure Standby</button>
    <button class="btn-kill" onclick="controlBot('kill-switch')">Abort Cycle</button>
  </div>

  <div class="grid">
    <div class="card">
      <div class="label">Net Equity <span>REALTIME</span></div>
      <div id="equity" class="value">--</div>
      <div id="pnl" class="pnl-chip">--</div>
    </div>
    <div class="card">
      <div class="label">Market Regime <span>V-SCORE</span></div>
      <div id="regime" class="value">--</div>
      <div id="risk" class="pnl-chip">--</div>
    </div>
    <div class="card">
      <div class="label">Utilization <span>MAX 1.0x</span></div>
      <div id="exposure" class="value">--</div>
      <div id="exposurePct" class="pnl-chip">--</div>
    </div>
    <div class="card">
      <div class="label">Growth Performance <span>NETT</span></div>
      <div id="totalPnl" class="value">--</div>
      <div class="pnl-chip">Kenyan Time (GMT+3)</div>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: 3fr 2fr;">
    <div class="card">
      <h2 class="section-title">Active Fleet Positions</h2>
      <table>
        <thead>
          <tr>
            <th>Asset</th>
            <th>Qty</th>
            <th>Entry</th>
            <th>Mark</th>
            <th>Weight</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody id="positionsTable"></tbody>
      </table>
    </div>
    <div class="card">
        <h2 class="section-title">Fleet Efficiency</h2>
        <div style="display: flex; flex-direction: column; gap: 20px;">
            <div>
                <div class="label">Execution Count</div>
                <div id="trades" style="font-size: 20px; font-weight: 700;">--</div>
            </div>
            <div>
                <div class="label">Alpha Probability</div>
                <div id="winRate" style="font-size: 20px; font-weight: 700; color: var(--accent);">--</div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                <div>
                   <div class="label">Take Profits</div>
                   <div id="takeProfits" class="value positive" style="font-size: 20px;">0</div>
                </div>
                <div>
                   <div class="label">Stop Losses</div>
                   <div id="stopLosses" class="value negative" style="font-size: 20px;">0</div>
                </div>
            </div>
        </div>
    </div>
  </div>

  <div class="grid">
    <div class="card" style="grid-column: span 2;">
        <h2 class="section-title">Live Execution Stream (GMT+3)</h2>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Asset</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Fill</th>
              <th>Strategy</th>
              <th>Conf</th>
            </tr>
          </thead>
          <tbody id="latestTrades"></tbody>
        </table>
    </div>
    <div class="card">
        <h2 class="section-title">Evolutionary Scores</h2>
        <table>
          <thead>
            <tr>
              <th>Agent ID</th>
              <th>Net Alpha</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="strategyTable"></tbody>
        </table>
    </div>
  </div>

  <div class="footer">
    Restricted System Access • Quantitative Research OS • Institutional Standard
  </div>

<script>
function money(value) {
  const n = Number(value ?? 0);
  const sign = n >= 0 ? '+' : '-';
  return sign + '$' + Math.abs(n).toFixed(3);
}

function dollars(value) {
  return '$' + Number(value ?? 0).toFixed(2);
}

function pnlClass(value) {
  return Number(value ?? 0) >= 0 ? 'positive' : 'negative';
}

async function controlBot(action) {
  const confirmed = action === 'kill-switch' ? confirm('ACTIVATE EMERGENCY HALT?') : true;
  if (!confirmed) return;
  await fetch('/' + action, { method: 'POST' });
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

    // Header State
    const badge = document.getElementById('botStateBadge');
    const text = document.getElementById('stateText');
    if (data.paused) {
        badge.className = 'state-badge state-paused';
        text.textContent = 'SECURE STANDBY';
    } else {
        badge.className = 'state-badge state-running';
        text.textContent = 'SYSTEM OPERATIONAL';
    }

    // Top Row
    document.getElementById('equity').textContent = dollars(p.equity);
    document.getElementById('pnl').innerHTML = `<span class="${pnlClass(p.realized_pnl)}">${money(p.realized_pnl)} Realized</span>`;
    
    document.getElementById('regime').textContent = p.regime ?? 'ANALYZING';
    document.getElementById('risk').textContent = data.risk_status ?? 'SECURE';
    document.getElementById('risk').className = 'pnl-chip ' + String(data.risk_status ?? 'safe').toLowerCase();

    document.getElementById('exposure').textContent = dollars(p.exposure);
    document.getElementById('exposurePct').textContent = (Number(p.exposure_pct ?? 0) * 100).toFixed(2) + '% Load';

    const totalPnl = document.getElementById('totalPnl');
    totalPnl.textContent = money(p.total_pnl);
    totalPnl.className = 'value ' + pnlClass(p.total_pnl);

    // Sidebar Stats
    document.getElementById('trades').innerHTML = `<span class="positive">${t.buy_count} L</span> / <span class="negative">${t.sell_count} S</span> (${t.total_trades})`;
    document.getElementById('winRate').textContent = (Number(perf.win_rate_estimate ?? 0) * 100).toFixed(1) + '% Success';
    document.getElementById('takeProfits').textContent = perf.take_profit_count ?? 0;
    document.getElementById('stopLosses').textContent = perf.stop_loss_count ?? 0;

    // Tables
    const posTable = document.getElementById('positionsTable');
    posTable.innerHTML = '';
    if (!positions.length) {
        posTable.innerHTML = '<tr><td colspan="6" style="text-align:center; opacity:0.5; padding:40px;">No Active Fleet Positions</td></tr>';
    } else {
        positions.forEach(pos => {
            posTable.innerHTML += `
                <tr>
                  <td><span class="symbol-pill">${pos.symbol}</span></td>
                  <td>${pos.quantity.toFixed(4)}</td>
                  <td>${pos.avg_entry.toFixed(4)}</td>
                  <td>${pos.mark_price.toFixed(4)}</td>
                  <td>${(pos.exposure / p.equity * 100).toFixed(1)}%</td>
                  <td class="${pnlClass(pos.unrealized_pnl)}">${money(pos.unrealized_pnl)}</td>
                </tr>
            `;
        });
    }

    const tradeTable = document.getElementById('latestTrades');
    tradeTable.innerHTML = '';
    (t.latest_trades || []).slice(0, 15).forEach(trade => {
        tradeTable.innerHTML += `
            <tr>
              <td style="font-size:12px; color:var(--text-dim)">${trade.created_at.split(' ')[1]}</td>
              <td>${trade.symbol}</td>
              <td class="${trade.side}">${trade.side.toUpperCase()}</td>
              <td>${trade.quantity.toFixed(5)}</td>
              <td>${trade.fill_price.toFixed(4)}</td>
              <td><span class="strategy-pill">${trade.strategy}</span></td>
              <td style="color:var(--accent)">${(trade.confidence * 100).toFixed(0)}%</td>
            </tr>
        `;
    });

    const stratTable = document.getElementById('strategyTable');
    stratTable.innerHTML = '';
    (data.strategy_scores || []).slice(0, 8).forEach(s => {
        stratTable.innerHTML += `
            <tr>
              <td>${s.strategy}</td>
              <td class="${pnlClass(s.score)}">${money(s.score)}</td>
              <td><span class="pnl-chip">${s.status}</span></td>
            </tr>
        `;
    });

  } catch (e) {
    console.error("Dashboard Sync Failed", e);
  }
}

setInterval(loadDashboard, 10000);
loadDashboard();
</script>
</body>
</html>
"""
