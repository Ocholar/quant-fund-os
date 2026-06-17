from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from core.db import engine
from core.config import settings
from services.metrics import metrics_app
from core.control import is_paused, pause_bot, resume_bot, pause_reason, get_control_state
from services.telegram import send_telegram_alert

# === QFOS_DIRECT_HELPERS_START ===
QFOS_EXIT_WORDS = (
    "stop_loss",
    "take_profit",
    "risk_off",
    "emergency",
    "breakeven",
    "time_stop",
    "exit",
    "trailing_stop",
    "manual_exit",
    "liquidation",
)

def qfos_normalize_trade_row(row):
    try:
        d = dict(row)
    except Exception:
        return row

    raw_strategy = d.get("raw_strategy", d.get("strategy"))
    raw_l = str(raw_strategy or "").strip().lower()
    is_exit = any(word in raw_l for word in QFOS_EXIT_WORDS)

    d["raw_strategy"] = raw_strategy
    d["is_exit"] = bool(is_exit)

    if is_exit:
        d["exit_reason"] = d.get("exit_reason") or raw_strategy
        d["entry_strategy"] = d.get("entry_strategy")
        d["display_strategy"] = d.get("display_strategy") or d["exit_reason"]
    else:
        d["exit_reason"] = None
        d["entry_strategy"] = d.get("entry_strategy") or raw_strategy
        d["display_strategy"] = d.get("display_strategy") or d["entry_strategy"]

    return d

def qfos_normalize_trade_list(rows):
    return [qfos_normalize_trade_row(r) for r in (rows or [])]

def qfos_normalize_payload(payload):
    if isinstance(payload, list):
        return qfos_normalize_trade_list(payload)

    if isinstance(payload, dict):
        if isinstance(payload.get("value"), list):
            payload["value"] = qfos_normalize_trade_list(payload["value"])

        if isinstance(payload.get("trades"), list):
            payload["trades"] = qfos_normalize_trade_list(payload["trades"])

        if isinstance(payload.get("latest_trades"), list):
            payload["latest_trades"] = qfos_normalize_trade_list(payload["latest_trades"])

        trading = payload.get("trading")
        if isinstance(trading, dict) and isinstance(trading.get("latest_trades"), list):
            trading["latest_trades"] = qfos_normalize_trade_list(trading["latest_trades"])

        if payload.get("paused") is True:
            payload["bot_state"] = "PAUSED"
            payload["status_label"] = "PAUSED"
            if isinstance(trading, dict):
                trading["bot_state"] = "PAUSED"
                trading["status_label"] = "PAUSED"

    return payload
# === QFOS_DIRECT_HELPERS_END ===








# === FORCE TRADE NORMALIZER PATCH ===
EXIT_REASON_STRATEGIES = {
    "stop_loss",
    "stop_loss_exit",
    "adaptive_stop_loss",
    "take_profit",
    "adaptive_take_profit",
    "risk_off_exit",
    "emergency_exposure_reduction",
    "breakeven_protection_exit",
    "time_stop_exit",
}

def normalize_trade_for_dashboard(t):
    try:
        d = dict(t)
    except Exception:
        d = {}
        for k in (
            "symbol", "side", "quantity", "fill_price", "slippage_bps",
            "strategy", "confidence", "live", "created_at"
        ):
            try:
                d[k] = getattr(t, k)
            except Exception:
                pass

    strategy = str(d.get("strategy") or "").strip()
    side = str(d.get("side") or "").strip().lower()
    strategy_l = strategy.lower()

    d["raw_strategy"] = strategy

    if side == "sell" and strategy_l in EXIT_REASON_STRATEGIES:
        d["exit_reason"] = strategy_l
        d["entry_strategy"] = None
        d["display_strategy"] = strategy_l
        d["is_exit"] = True
    else:
        d["exit_reason"] = None
        d["entry_strategy"] = strategy
        d["display_strategy"] = strategy
        d["is_exit"] = False

    return d
def normalize_trades_for_dashboard(rows):
    return qfos_normalize_payload([normalize_trade_for_dashboard(x) for x in (rows or [])])

def status_label_from_api(data):
    if bool(data.get("paused")):
        return "PAUSED"
    if bool(data.get("kill_switch") or data.get("killed")):
        return "KILLED"
    return "RUNNING"
# === END FORCE TRADE NORMALIZER PATCH ===


# === ONE-PATCH DASHBOARD/EXIT NORMALIZATION ===
EXIT_REASON_STRATEGIES = {
    "stop_loss",
    "stop_loss_exit",
    "adaptive_stop_loss",
    "take_profit",
    "adaptive_take_profit",
    "risk_off_exit",
    "emergency_exposure_reduction",
    "breakeven_protection_exit",
    "time_stop_exit",
}

STOP_LOSS_EXIT_STRATEGIES = {
    "stop_loss",
    "stop_loss_exit",
    "adaptive_stop_loss",
}

TAKE_PROFIT_EXIT_STRATEGIES = {
    "take_profit",
    "adaptive_take_profit",
}

RISK_OFF_EXIT_STRATEGIES = {
    "risk_off_exit",
}

EMERGENCY_EXIT_STRATEGIES = {
    "emergency_exposure_reduction",
}


def _trade_to_plain_dict(row):
    try:
        d = dict(row)
    except Exception:
        d = {}
        for k in ("symbol", "side", "quantity", "fill_price", "slippage_bps", "strategy", "confidence", "live", "created_at"):
            try:
                d[k] = getattr(row, k)
            except Exception:
                pass
    return d
def _normalize_trade_for_dashboard(row):
    """
    Do not let exit reasons pretend to be entry strategies.
    Keeps old DB schema working, but adds:
      raw_strategy
      exit_reason
      entry_strategy
      display_strategy
    """
    d = _trade_to_plain_dict(row)
    raw = str(d.get("strategy") or "").strip()
    side = str(d.get("side") or "").lower().strip()
    raw_l = raw.lower()

    d["raw_strategy"] = raw

    if side == "sell" and raw_l in EXIT_REASON_STRATEGIES:
        d["exit_reason"] = raw_l
        d["entry_strategy"] = d.get("entry_strategy") or None
        d["display_strategy"] = raw_l
    else:
        d["exit_reason"] = d.get("exit_reason") or None
        d["entry_strategy"] = raw
        d["display_strategy"] = raw

    return d
def _normalize_trade_list_for_dashboard(rows):
    return qfos_normalize_payload([_normalize_trade_for_dashboard(r) for r in (rows or [])])


def _strategy_is_real_entry_strategy(strategy):
    s = str(strategy or "").strip().lower()
    return bool(s) and s not in EXIT_REASON_STRATEGIES


def _bot_state_from_payload(payload):
    paused = bool(payload.get("paused", False))
    killed = bool(payload.get("kill_switch", False) or payload.get("killed", False))
    if killed:
        return "KILLED"
    if paused:
        return "PAUSED"
    return "RUNNING"
# === END ONE-PATCH DASHBOARD/EXIT NORMALIZATION ===


app = FastAPI(title="Quant Fund OS")

# === QFOS_FORCE_MIDDLEWARE_PATCH ===
try:
    from starlette.responses import JSONResponse
    import json as _qfos_json

    @app.middleware("http")
    async def qfos_force_response_normalizer(request, call_next):
        response = await call_next(request)
        path = str(request.url.path)

        if not (path.endswith("/status") or path.endswith("/trades")):
            return response

        if "application/json" not in response.headers.get("content-type", ""):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            payload = _qfos_json.loads(body.decode("utf-8"))
            payload = qfos_normalize_payload(payload)
        except Exception as exc:
            print("QFOS force middleware failed:", exc)
            return response

        headers = dict(response.headers)
        headers.pop("content-length", None)

        return JSONResponse(content=payload, status_code=response.status_code, headers=headers)
except Exception as exc:
    print("QFOS force middleware install failed:", exc)
# === QFOS_FORCE_MIDDLEWARE_PATCH_END ===






# === QFOS FINAL RESPONSE NORMALIZER PATCH ===
QFOS_EXIT_REASON_STRATEGIES = {
    "stop_loss",
    "stop_loss_exit",
    "adaptive_stop_loss",
    "take_profit",
    "adaptive_take_profit",
    "risk_off_exit",
    "emergency_exposure_reduction",
    "breakeven_protection_exit",
    "time_stop_exit",
}

def qfos_normalize_trade_row(row):
    if not isinstance(row, dict):
        return row
    d = dict(row)
    strategy = str(d.get("strategy") or "").strip()
    side = str(d.get("side") or "").strip().lower()
    strategy_l = strategy.lower()

    d["raw_strategy"] = strategy

    if side == "sell" and strategy_l in QFOS_EXIT_REASON_STRATEGIES:
        d["exit_reason"] = strategy_l
        d["entry_strategy"] = None
        d["display_strategy"] = strategy_l
        d["is_exit"] = True
    else:
        d["exit_reason"] = None
        d["entry_strategy"] = strategy
        d["display_strategy"] = strategy
        d["is_exit"] = False

    return d
def qfos_normalize_trade_list(rows):
    if not isinstance(rows, list):
        return rows
    return qfos_normalize_payload([qfos_normalize_trade_row(x) for x in rows])

def qfos_normalize_api_payload(payload):
    if not isinstance(payload, dict):
        return payload

    # /trades shape: {"value": [...], "Count": 50}
    if isinstance(payload.get("value"), list):
        payload["value"] = qfos_normalize_trade_list(payload["value"])

    # /status shape: {"trading": {"latest_trades": [...]}}
    trading = payload.get("trading")
    if isinstance(trading, dict) and isinstance(trading.get("latest_trades"), list):
        trading["latest_trades"] = qfos_normalize_trade_list(trading["latest_trades"])

    # Force truthful bot state labels
    if payload.get("paused") is True:
        payload["bot_state"] = "PAUSED"
        payload["status_label"] = "PAUSED"
        payload["running"] = False
    elif payload.get("kill_switch") is True or payload.get("killed") is True:
        payload["bot_state"] = "KILLED"
        payload["status_label"] = "KILLED"
        payload["running"] = False
    elif "bot_state" not in payload:
        payload["bot_state"] = "RUNNING"
        payload["status_label"] = "RUNNING"
        payload["running"] = True

    return payload

@app.middleware("http")
async def qfos_trade_response_normalizer(request, call_next):
    response = await call_next(request)

    path = request.url.path
    if path not in {"/trades", "/status"}:
        return response
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    try:
        import json
        payload = json.loads(body.decode("utf-8"))
        payload = qfos_normalize_api_payload(payload)
        return JSONResponse(
            content=payload,
            status_code=response.status_code,
            headers={
                k: v for k, v in response.headers.items()
                if k.lower() not in {"content-length", "content-type"}
            },
        )
    except Exception as e:
        return JSONResponse(
            content={
                "error": "qfos_response_normalizer_failed",
                "detail": str(e),
            },
            status_code=500,
        )
# === END QFOS FINAL RESPONSE NORMALIZER PATCH ===


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
        WHERE quantity > 0.00000001
          AND exposure >= 0.05
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
            SUM(CASE WHEN LOWER(TRIM(strategy)) IN ('take_profit', 'adaptive_take_profit', 'single_full_take_profit', 'fast_take_profit_stage_1', 'fast_take_profit_stage_2') THEN 1 ELSE 0 END) AS take_profit_count,
            SUM(CASE WHEN LOWER(TRIM(strategy)) IN ('stop_loss', 'adaptive_stop_loss', 'stop_loss_exit') THEN 1 ELSE 0 END) AS stop_loss_count,
            SUM(CASE WHEN LOWER(TRIM(strategy)) IN ('risk_off_exit') THEN 1 ELSE 0 END) AS risk_off_exit_count,
            SUM(CASE WHEN LOWER(TRIM(strategy)) IN ('emergency_exposure_reduction') THEN 1 ELSE 0 END) AS emergency_exit_count
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
        "win_rate_estimate": round(win_rate_estimate, 4),
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

# ============================================================
# QFO_API_TRADES_TABLE_GUARD_V1
#
# Fix:
# - Fresh paper reset can leave SQLite without a trades table.
# - /status must not crash with:
#       sqlite3.OperationalError: no such table: trades
# - Create a minimal trades table if missing.
# - If query still fails, return [] for latest_trades.
# ============================================================

def qfo_api_ensure_trades_table(conn):
    try:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                quantity REAL DEFAULT 0,
                fill_price REAL DEFAULT 0,
                slippage_bps REAL DEFAULT 0,
                strategy TEXT,
                confidence REAL DEFAULT 0,
                live BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        try:
            conn.commit()
        except Exception:
            pass
        return True
    except Exception as e:
        try:
            print(f"[API_DB_GUARD] trades table ensure failed: {e}")
        except Exception:
            pass
        return False

def qfo_api_fetch_latest_trades(conn, limit=10):
    try:
        qfo_api_ensure_trades_table(conn)
        rows = conn.execute(text("""
            SELECT symbol, side, quantity, fill_price, slippage_bps,
                   strategy, confidence, live, created_at
            FROM trades
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": int(limit)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        try:
            print(f"[API_DB_GUARD] latest_trades fallback [] because: {e}")
        except Exception:
            pass
        return []

# ============================================================
# End QFO_API_TRADES_TABLE_GUARD_V1
# ============================================================





# ============================================================
# QFO_STATUS_PREENSURE_TRADES_TABLE_V1
#
# Fix:
# - After a fresh reset, SQLite may not yet have a trades table.
# - /status was crashing when latest_trades tried SELECT FROM trades.
# - This guard creates the minimal trades table before status queries run.
# ============================================================
def qfo_status_preensure_trades_table(conn):
    try:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                quantity REAL DEFAULT 0,
                fill_price REAL DEFAULT 0,
                slippage_bps REAL DEFAULT 0,
                strategy TEXT,
                confidence REAL DEFAULT 0,
                live BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # Do NOT call conn.commit() here.
        # get_status_payload() runs this inside engine.begin().
        # Committing here closes that transaction and breaks later status queries.
        return True
    except Exception as e:
        try:
            print(f"[STATUS_TRADES_GUARD] ensure failed: {e}")
        except Exception:
            pass
        return False
# ============================================================
# End QFO_STATUS_PREENSURE_TRADES_TABLE_V1
# ============================================================




# ============================================================
# ============================================================



def get_status_payload():
    with engine.begin() as conn:
        try:
            qfo_status_preensure_trades_table(conn)
        except Exception as e:
            try:
                print(f"[STATUS_TRADES_GUARD] preensure skipped: {e}")
            except Exception:
                pass
        ensure_positions_table(conn)

        latest = conn.execute(text("""
            SELECT equity, cash, exposure, drawdown, regime, created_at
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

        latest_trades = qfo_api_fetch_latest_trades(conn, 10)

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
        "mode": "live" if settings.live_trading else "paper",
        "live_trading": settings.live_trading,
        "exchange": "mexc" if settings.mexc_api_key else "simulated",
        "exchange_type": settings.mexc_exchange_type,
        "leverage": settings.mexc_leverage,
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
            "latest_trades": qfos_normalize_trade_list([dict(t) for t in latest_trades]),
        },
        **_count_exit_strategies(),
        "risk_rules": {
            "max_total_exposure_pct": float(getattr(settings, "max_total_exposure_pct", 0.08)),
            "max_symbol_exposure_pct": float(getattr(settings, "max_symbol_exposure_pct", 0.03)),
            "caution_exposure_pct": float(getattr(settings, "caution_exposure_pct", 0.06)),
            "blocked_drawdown": float(getattr(settings, "blocked_drawdown", -0.05)),
            "caution_drawdown": float(getattr(settings, "caution_drawdown", -0.02)),
            "sideways_max_entries_per_hour": int(getattr(settings, "sideways_max_entries_per_hour", 3)),
            "sideways_min_confidence": float(getattr(settings, "sideways_min_confidence", 0.75)),
            "max_trades_per_symbol": int(getattr(settings, "max_trades_per_symbol", 3)),
            "trade_count_window_hours": float(getattr(settings, "trade_count_window_hours", 2)),
            "entry_quality_top_n": int(getattr(settings, "entry_quality_top_n", 2)),
            "entry_min_signal_sideways": float(getattr(settings, "entry_min_signal_sideways", 0.025)),
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

    return qfos_normalize_trade_list([dict(r) for r in rows])


@app.get("/portfolio")
def portfolio(limit: int = 100):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT equity, cash, exposure, drawdown, regime, created_at
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return qfos_normalize_trade_list([dict(r) for r in rows])


@app.get("/portfolio/latest")
def latest_portfolio():
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT equity, cash, exposure, drawdown, regime, created_at
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

    return normalize_trades_for_dashboard(dict)(row) if row else {
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



def _count_exit_strategies():
    """
    Counts real exit types from the trades table.
    This fixes dashboard/status counters so adaptive exits are included.
    """
    counts = {
        "take_profit_count": 0,
        "stop_loss_count": 0,
        "breakeven_protection_exit_count": 0,
        "time_stop_exit_count": 0,
    }

    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT strategy, COUNT(*) AS count
                FROM trades
                WHERE side = 'sell'
                GROUP BY strategy
            """)).mappings().all()

        for row in rows:
            strategy = str(row.get("strategy") or "")
            count = int(row.get("count") or 0)

            if strategy in (
                "take_profit",
                "adaptive_take_profit",
                "single_full_take_profit",
                "fast_take_profit_stage_1",
                "fast_take_profit_stage_2",
            ):
                counts["take_profit_count"] += count

            elif strategy in (
                "stop_loss",
                "adaptive_stop_loss",
            ):
                counts["stop_loss_count"] += count

            elif strategy == "breakeven_protection_exit":
                counts["breakeven_protection_exit_count"] += count

            elif strategy == "time_stop_exit":
                counts["time_stop_exit_count"] += count

    except Exception as e:
        print("EXIT COUNTER ERROR:", e)

    return counts


@app.get("/status")

def status():
    payload = get_status_payload()
    control = get_control_state()
    paused = control["paused"]

    payload["paused"] = paused
    payload["pause_reason"] = control.get("reason") or ""
    payload["bot_state"] = "PAUSED" if paused else "RUNNING"
    payload["status_label"] = payload["bot_state"]
    payload["controls"] = {
        "pause": "/pause",
        "resume": "/resume",
        "kill_switch": "/kill-switch",
    }

    return qfos_normalize_payload(payload)


    payload["paused"] = paused
    payload["pause_reason"] = control.get("reason") or ""
    payload["bot_state"] = "PAUSED" if paused else "RUNNING"
    payload["status_label"] = payload["bot_state"]
    payload["controls"] = {
        "pause": "/pause",
        "resume": "/resume",
        "kill_switch": "/kill-switch",
    }
    return qfos_normalize_payload(payload)
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
      <div class="subtitle">Paper-first autonomous trading dashboard</div>
    </div>
    <div id="botStateBadge" class="state-badge">
       <div class="pulse"></div>
       <span id="stateText">Loading...</span>
    </div>
  </header>

  <div class="controls">
    <button class="btn-resume" onclick="controlBot('resume')">Resume</button>
    <button class="btn-pause" onclick="controlBot('pause')">Pause</button>
    <button class="btn-kill" onclick="controlBot('kill-switch')">Kill Switch</button>
  </div>

  <div class="grid">
    <div class="card">
      <div class="label">Equity</div>
      <div id="equity" class="value">--</div>
      <div id="pnl" class="pnl-chip">--</div>
    </div>
    <div class="card">
      <div class="label">Regime</div>
      <div id="regime" class="value">--</div>
      <div id="risk" class="pnl-chip">--</div>
    </div>
    <div class="card">
      <div class="label">Exposure</div>
      <div id="exposure" class="value">--</div>
      <div id="exposurePct" class="pnl-chip">--</div>
    </div>
    <div class="card">
      <div class="label">Total Real PnL</div>
      <div id="totalPnl" class="value">--</div>
      <div class="pnl-chip">Kenyan Time (GMT+3)</div>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: 3fr 2fr;">
    <div class="card">
      <h2 class="section-title">Open Positions</h2>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Quantity</th>
            <th>Avg Entry</th>
            <th>Mark Price</th>
            <th>Exposure %</th>
            <th>Unrealized PnL</th>
          </tr>
        </thead>
        <tbody id="positionsTable"></tbody>
      </table>
    </div>
    <div class="card">
        <h2 class="section-title">Trading Metrics</h2>
        <div style="display: flex; flex-direction: column; gap: 20px;">
            <div>
                <div class="label">Trades</div>
                <div id="trades" style="font-size: 20px; font-weight: 700;">--</div>
            </div>
            <div>
                <div class="label">Win Rate Estimate</div>
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
        <h2 class="section-title">Latest Trades</h2>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Fill Price</th>
              <th>Strategy</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody id="latestTrades"></tbody>
        </table>
    </div>
    <div class="card">
        <h2 class="section-title">Strategy Performance</h2>
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Score</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="strategyTable"></tbody>
        </table>
    </div>
  </div>

  <div class="footer">
    Live trading is OFF. This dashboard is running in paper mode.
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
        text.textContent = 'PAUSED';
    } else {
        badge.className = 'state-badge state-running';
        text.textContent = qfosBotState(data);
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
    document.getElementById('trades').innerHTML = `<span class="positive">Buy ${t.buy_count}</span> / <span class="negative">Sell ${t.sell_count}</span> (${t.total_trades})`;
    document.getElementById('winRate').textContent = (Number(perf.win_rate_estimate ?? 0) * 100).toFixed(1) + '%';
    document.getElementById('takeProfits').textContent = perf.take_profit_count ?? 0;
    document.getElementById('stopLosses').textContent = perf.stop_loss_count ?? 0;

    // Tables
    const posTable = document.getElementById('positionsTable');
    posTable.innerHTML = '';
    if (!positions.length) {
        posTable.innerHTML = '<tr><td colspan="6" style="text-align:center; opacity:0.5; padding:40px;">No open positions</td></tr>';
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
              <td><span class="strategy-pill">${(trade.display_strategy || trade.strategy)}</span></td>
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

function qfosBotState(data) {
  if (!data) return "UNKNOWN";
  if (data.bot_state) return String(data.bot_state).toUpperCase();
  if (data.paused === true) return "PAUSED";
  if (data.paused === false) return "RUNNING";
  return "UNKNOWN";
}
function qfosApplyBotState(data) {
  const state = qfosBotState(data);
  const badge =
    document.getElementById("botStatus") ||
    document.getElementById("statusBadge") ||
    document.getElementById("runningStatus") ||
    document.querySelector("[data-bot-status]");
  if (badge) {
    badge.textContent = state;
    badge.classList.remove("running", "paused", "killed", "safe", "danger");
    badge.classList.add(state.toLowerCase());
  }
}


const EXIT_REASON_STRATEGIES_JS = new Set([
  "stop_loss","stop_loss_exit","adaptive_stop_loss",
  "take_profit","adaptive_take_profit",
  "risk_off_exit","emergency_exposure_reduction",
  "breakeven_protection_exit","time_stop_exit"
]);

</script>

<script>
async function qfosForceStatusRefresh() {
  try {
    const r = await fetch('/status', {cache: 'no-store'});
    const data = await r.json();
    const state = data.bot_state || (data.paused ? 'PAUSED' : 'RUNNING');

    const candidates = [
      document.getElementById('botStatus'),
      document.getElementById('statusBadge'),
      document.getElementById('runningStatus'),
      document.querySelector('[data-bot-status]')
    ].filter(Boolean);

    for (const el of candidates) {
      el.textContent = state;
      el.classList.remove('running', 'paused', 'killed');
      el.classList.add(String(state).toLowerCase());
    }
  } catch (e) {
    console.log('status refresh failed', e);
  }
}
setInterval(qfosForceStatusRefresh, 3000);
qfosForceStatusRefresh();
</script>

</body>
</html>
"""

# === QFOS_FINAL_HELPER_OVERRIDE_START ===
# Final override: route functions already call qfos_normalize_trade_list()
# and qfos_normalize_payload(). These definitions must come last so they
# override any stale/broken earlier definitions.

QFOS_EXIT_WORDS = (
    "stop_loss",
    "take_profit",
    "risk_off",
    "emergency",
    "breakeven",
    "time_stop",
    "exit",
    "trailing_stop",
    "manual_exit",
    "liquidation",
)

def qfos_normalize_trade_row(row):
    try:
        d = dict(row)
    except Exception:
        return row

    raw_strategy = d.get("raw_strategy", d.get("strategy"))
    raw_l = str(raw_strategy or "").strip().lower()

    is_exit = any(word in raw_l for word in QFOS_EXIT_WORDS)

    d["raw_strategy"] = raw_strategy
    d["is_exit"] = bool(is_exit)

    if is_exit:
        d["exit_reason"] = d.get("exit_reason") or raw_strategy
        d["entry_strategy"] = d.get("entry_strategy")
        d["display_strategy"] = d.get("display_strategy") or d["exit_reason"]
    else:
        d["exit_reason"] = None
        d["entry_strategy"] = d.get("entry_strategy") or raw_strategy
        d["display_strategy"] = d.get("display_strategy") or d["entry_strategy"]

    return d

def qfos_normalize_trade_list(rows):
    return [qfos_normalize_trade_row(r) for r in (rows or [])]

def qfos_normalize_payload(payload):
    if isinstance(payload, list):
        return qfos_normalize_trade_list(payload)

    if not isinstance(payload, dict):
        return payload

    if isinstance(payload.get("value"), list):
        payload["value"] = qfos_normalize_trade_list(payload["value"])

    if isinstance(payload.get("trades"), list):
        payload["trades"] = qfos_normalize_trade_list(payload["trades"])

    if isinstance(payload.get("latest_trades"), list):
        payload["latest_trades"] = qfos_normalize_trade_list(payload["latest_trades"])

    trading = payload.get("trading")
    if isinstance(trading, dict) and isinstance(trading.get("latest_trades"), list):
        trading["latest_trades"] = qfos_normalize_trade_list(trading["latest_trades"])

    if payload.get("paused") is True:
        payload["bot_state"] = "PAUSED"
        payload["status_label"] = "PAUSED"

        if isinstance(trading, dict):
            trading["bot_state"] = "PAUSED"
            trading["status_label"] = "PAUSED"

    return payload

print("QFOS final helper override loaded.")
# === QFOS_FINAL_HELPER_OVERRIDE_END ===


# QFO_API_TRADES_TABLE_GUARD_V1 installed

# QFO_STATUS_PREENSURE_TRADES_TABLE_V1 installed

# QFO_FIX_STATUS_TRANSACTION_COMMIT_V1 installed

# ============================================================
# QFO_DASHBOARD_TRUTH_V2_START
# Purpose:
# - Count ALL stop-loss style exits correctly, including sideways_scalp_stop_loss.
# - Calculate win rate from closed exits, not all BUY/SELL rows.
# - Recompute realized PnL from actual trade fills using FIFO.
# - Keep latest trades display defensive across DB schema differences.
# ============================================================

def qfo_api__safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def qfo_api__safe_upper(value):
    try:
        return str(value or "").upper()
    except Exception:
        return ""


def qfo_api__safe_lower(value):
    try:
        return str(value or "").lower()
    except Exception:
        return ""


def qfo_api__is_stop_loss_strategy(strategy):
    s = qfo_api__safe_lower(strategy)

    # Covers:
    # - stop_loss
    # - sideways_scalp_stop_loss
    # - quality_initial_stop_loss
    # - initial_stop_loss
    # - initial_stop
    # - hard_stop
    # - emergency_stop
    return (
        "stop_loss" in s
        or "stoploss" in s
        or "initial_stop" in s
        or "hard_stop" in s
        or "emergency_stop" in s
    )


def qfo_api__is_take_profit_strategy(strategy):
    s = qfo_api__safe_lower(strategy)

    # Covers:
    # - take_profit
    # - adaptive_take_profit
    # - full_take_profit
    # - partial_take_profit
    # - trailing_profit_exit
    # - profit_exit
    return (
        "take_profit" in s
        or "profit_exit" in s
        or "trailing_profit" in s
        or "adaptive_take_profit" in s
    )


def qfo_api__trade_sort_key(row):
    try:
        return (
            str(row.get("created_at") or ""),
            int(row.get("id") or 0),
        )
    except Exception:
        return ("", 0)


def qfo_api__fetch_all_trades_for_status(conn):
    try:
        rows = conn.execute(text("""
            SELECT *
            FROM trades
            ORDER BY created_at ASC, id ASC
        """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        try:
            print(f"[DASHBOARD_TRUTH] fetch trades failed: {e}")
        except Exception:
            pass
        return []


def qfo_api__compute_fifo_realized_pnl(trades):
    # Spot-long FIFO matching:
    # BUY opens inventory.
    # SELL closes oldest inventory for that symbol.
    inventory = {}
    realized = 0.0

    for row in sorted(trades, key=qfo_api__trade_sort_key):
        symbol = str(row.get("symbol") or "")
        side = qfo_api__safe_upper(row.get("side"))
        qty = qfo_api__safe_float(row.get("quantity"))
        price = qfo_api__safe_float(row.get("fill_price"))

        if not symbol or qty <= 0 or price <= 0:
            continue

        if side == "BUY":
            inventory.setdefault(symbol, []).append([qty, price])
            continue

        if side != "SELL":
            continue

        remaining = qty
        lots = inventory.setdefault(symbol, [])

        while remaining > 0 and lots:
            lot_qty, lot_price = lots[0]
            matched = min(remaining, lot_qty)

            realized += matched * (price - lot_price)

            lot_qty -= matched
            remaining -= matched

            if lot_qty <= 1e-12:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty

        # If a SELL has no matching BUY, ignore unmatched quantity.
        # This avoids inventing fake PnL from corrupted or reset history.

    return realized


def qfo_api__compute_unrealized_from_positions(conn):
    try:
        rows = conn.execute(text("SELECT * FROM positions")).mappings().all()
    except Exception:
        return 0.0

    total = 0.0

    for row in rows:
        r = dict(row)

        # Prefer stored unrealized_pnl if available.
        if "unrealized_pnl" in r and r.get("unrealized_pnl") is not None:
            total += qfo_api__safe_float(r.get("unrealized_pnl"))
            continue

        qty = qfo_api__safe_float(r.get("quantity"))
        avg_entry = qfo_api__safe_float(r.get("avg_entry"))
        mark_price = qfo_api__safe_float(r.get("mark_price"))

        if qty and avg_entry and mark_price:
            total += qty * (mark_price - avg_entry)

    return total


def qfo_api_fetch_latest_trades(conn, limit=10):
    # Defensive latest-trades helper used by get_status_payload().
    # Do not commit here. get_status_payload() runs inside engine.begin().
    try:
        rows = conn.execute(text("""
            SELECT *
            FROM trades
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
        """), {"limit": int(limit)}).mappings().all()
    except Exception as e:
        try:
            print(f"[DASHBOARD_TRUTH] latest trades fetch failed: {e}")
        except Exception:
            pass
        return []

    out = []
    for row in rows:
        r = dict(row)
        out.append({
            "time": r.get("created_at"),
            "created_at": r.get("created_at"),
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "quantity": round(qfo_api__safe_float(r.get("quantity")), 8),
            "fill_price": round(qfo_api__safe_float(r.get("fill_price")), 8),
            "strategy": r.get("strategy"),
            "confidence": round(qfo_api__safe_float(r.get("confidence")), 4),
            "live": bool(r.get("live")) if r.get("live") is not None else False,
        })

    return out


def get_performance(conn, equity=100.0):
    # Dashboard truth source:
    # - total_trades = all BUY + SELL trade rows.
    # - win_rate = take-profit exits / closed outcome exits.
    # - stop_loss_count includes sideways_scalp_stop_loss and quality_initial_stop_loss.
    # - realized_pnl is recomputed from actual fills, not strategy labels.
    trades = qfo_api__fetch_all_trades_for_status(conn)

    buy_count = 0
    sell_count = 0
    take_profit_count = 0
    stop_loss_count = 0
    other_exit_count = 0

    for row in trades:
        side = qfo_api__safe_upper(row.get("side"))
        strategy = row.get("strategy")

        if side == "BUY":
            buy_count += 1
            continue

        if side == "SELL":
            sell_count += 1

            if qfo_api__is_stop_loss_strategy(strategy):
                stop_loss_count += 1
            elif qfo_api__is_take_profit_strategy(strategy):
                take_profit_count += 1
            else:
                other_exit_count += 1

    total_trades = buy_count + sell_count
    closed_outcome_count = take_profit_count + stop_loss_count

    if closed_outcome_count > 0:
        win_rate = take_profit_count / closed_outcome_count
    else:
        win_rate = 0.0

    realized_pnl = qfo_api__compute_fifo_realized_pnl(trades)
    unrealized_pnl = qfo_api__compute_unrealized_from_positions(conn)
    total_pnl = realized_pnl + unrealized_pnl

    return {
        "total_trades": int(total_trades),
        "buy_count": int(buy_count),
        "sell_count": int(sell_count),

        # Keep both names because dashboard/API code may use either.
        "win_rate": round(win_rate, 4),
        "win_rate_estimate": round(win_rate, 4),

        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "total_pnl": round(total_pnl, 4),

        "take_profit_count": int(take_profit_count),
        "stop_loss_count": int(stop_loss_count),

        # Extra transparency for API consumers.
        "closed_outcome_count": int(closed_outcome_count),
        "other_exit_count": int(other_exit_count),
    }

# ============================================================
# QFO_DASHBOARD_TRUTH_V2_END
# ============================================================

