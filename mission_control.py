"""
mission_control.py â€” AI Mission Control Sidecar
SSE transport proven minimal-first, then wired to real trading state.
"""
import asyncio
import glob
import json
import os
import time
import redis
import socket
import logging
from logging.handlers import TimedRotatingFileHandler
from contextlib import asynccontextmanager

# ─── Logging Setup ────────────────────────────────────────────────────────
log_handler = TimedRotatingFileHandler("logs/mission_control.log", when="midnight", interval=1, backupCount=7)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger = logging.getLogger("MissionControl")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.addHandler(logging.StreamHandler()) # Also print to stdout for docker logs

def log_info(msg):
    logger.info(msg)

def log_error(msg):
    logger.error(msg)
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

load_dotenv(override=False)

# ─── Crash-safe task wrapper ───────────────────────────────────────────────────────────────
async def _safe_task(coro_fn, name: str):
    """Restart a background coroutine if it ever raises, so one crash
    doesn't bring down the whole sidecar process."""
    while True:
        try:
            await coro_fn()
        except Exception as exc:
            log_error(f"[{name}] CRASHED: {exc!r} — restarting in 5s")
            await asyncio.sleep(5)

# ─── Lifespan (replaces deprecated @app.on_event) ─────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_safe_task(tail_logs,       "tail_logs"))
    asyncio.create_task(_safe_task(poll_engine,     "poll_engine"))
    asyncio.create_task(_safe_task(poll_diagnostics,"poll_diagnostics"))
    asyncio.create_task(_safe_task(watchdog,        "watchdog"))
    yield

app = FastAPI(title="AI Mission Control", lifespan=lifespan)

# â”€â”€â”€ Leaderboard key validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_INVALID_LB_KEYS = frozenset({None, "", "null", "None", "?"})

def _valid_lb_key(symbol) -> str | None:
    """
    Return a normalised leaderboard key, or None if the symbol is unusable.
    Rejects: None, empty string, "null", "None", "?" and any string that
    normalises to those values.
    """
    if symbol in _INVALID_LB_KEYS:
        return None
    s = str(symbol).strip()
    if s in _INVALID_LB_KEYS:
        return None
    return s

# â”€â”€â”€ Shared in-memory state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
state = {
    "funnel": {"ranked": 0, "stage1": 0, "allocator": 0, "exec_started": 0, "opened": 0},
    "rejections": {},
    "leaderboard": {},
    "last_cycle_id": None,
    "timeline": [],
    "engine": {},
    "positions": [],
    "debug": {
        "events_processed": 0,
        "files_watched": 0,
        "last_event_type": "",
        "last_update": "",
    },
    # Diagnostics â€” updated each SSE tick
    "diagnostics": {
        "control_state": None,      # {paused, reason}
        "mexc_ok": None,            # bool
        "redis_ok": None,           # bool
        "postgres_ok": None,        # bool
        "engine_heartbeat_age_s": None,  # seconds since last log event
        "last_trade_ts": None,      # last trade timestamp (Kenya time)
        "last_log_wall_ts": None,   # wall-clock time of last new log line
    },
    # Watchdog â€” updated by the watchdog() task every 60 s
    "watchdog": {
        "stalled": False,
        "consecutive_stall_ticks": 0,
        "last_snapshot": {},
        "last_alert_ts": 0.0,
    },
}

file_offsets: dict[str, int] = {}


# â”€â”€â”€ Log processor (sync, fast) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _push_tl(time_str: str, text: str, color: str, raw_ts: str = ""):
    state["timeline"].append({"time": time_str, "text": text, "color": color, "raw_ts": raw_ts})
    if len(state["timeline"]) > 100:
        state["timeline"] = state["timeline"][-100:]



def process_event(evt: dict):
    etype   = evt.get("event_type", "")
    payload = evt.get("payload", {})
    ts      = evt.get("timestamp", "")

    # Base fallback time string
    time_str = ts[11:19] if len(ts) > 18 else ts
    # Adjust for Kenya time (+3 hours)
    try:
        if len(ts) > 18:
            dt_str = ts[:19].replace("Z", "")
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
            dt += timedelta(hours=3)
            time_str = dt.strftime("%H:%M:%S")
    except Exception:
        pass

    cid      = payload.get("candidate_id")
    rank     = payload.get("rank")
    symbol   = payload.get("symbol", "?")
    cycle_id = payload.get("cycle_id")

    # Validated leaderboard key â€” rejects None/"null"/"None"/"?"/"" etc.
    lb_key = _valid_lb_key(symbol)

    # Track wall clock of last new log line (for heartbeat age)
    state["diagnostics"]["last_log_wall_ts"] = time.monotonic()
    state["debug"]["events_processed"] += 1
    state["debug"]["last_event_type"]   = etype
    state["debug"]["last_update"]       = time_str

    if cycle_id and cycle_id != state["last_cycle_id"]:
        state["last_cycle_id"] = cycle_id
        _push_tl(time_str, f"ðŸŸ¢ Cycle #{cycle_id} started", "blue", ts)

    if etype == "candidate_ranked":
        state["funnel"]["ranked"] += 1
        if lb_key:
            state["leaderboard"][lb_key] = {
                "rank": rank, "symbol": symbol,
                "confidence": payload.get("confidence", 0),
                "status": "âšª Waiting",
                "timestamp": ts,
            }
        _push_tl(time_str, f"â„¹ Ranked {symbol} #{rank}", "blue", ts)

    elif etype == "candidate_filtered":
        stage  = payload.get("filter_stage", 0)
        reason = payload.get("reason", "OTHER")
        state["rejections"][reason] = state["rejections"].get(reason, 0) + 1
        if lb_key and lb_key in state["leaderboard"]:
            state["leaderboard"][lb_key]["status"] = f"ðŸ”´ {reason}"
        if stage > 1:
            state["funnel"]["stage1"] += 1
        _push_tl(time_str, f"âš  {symbol} rejected: {reason}", "yellow", ts)

    elif etype == "candidate_approved":
        state["funnel"]["stage1"]    += 1
        state["funnel"]["allocator"] += 1
        if lb_key and lb_key in state["leaderboard"]:
            state["leaderboard"][lb_key]["status"] = "ðŸŸ¢ Approved"
        _push_tl(time_str, f"âœ… {symbol} approved", "green", ts)

    elif etype == "trade_execution_started":
        state["funnel"]["exec_started"] += 1
        _push_tl(time_str, f"ðŸ’° BUY submitted: {symbol}", "blue", ts)

    elif etype == "trade_execution_failed":
        reason = payload.get("reason", "EXEC_FAILED")
        state["rejections"][reason] = state["rejections"].get(reason, 0) + 1
        if lb_key and lb_key in state["leaderboard"]:
            state["leaderboard"][lb_key]["status"] = "ðŸ”´ Failed"
        _push_tl(time_str, f"âœ• Execution failed: {reason}", "red", ts)

    elif etype == "trade_opened":
        state["funnel"]["opened"] += 1
        _push_tl(time_str, f"âœ“ Position opened: {symbol}", "green", ts)
        if lb_key and lb_key in state["leaderboard"]:
            state["leaderboard"][lb_key]["status"] = "ðŸŸ¢ Open"
        state["diagnostics"]["last_trade_ts"] = time_str

    elif etype == "trade_exited":
        _push_tl(time_str, f"ðŸ“¤ Position exited: {symbol}", "blue", ts)
        state["diagnostics"]["last_trade_ts"] = time_str


# â”€â”€â”€ Background tasks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def tail_logs():
    logs_dir   = Path("logs/candidates")
    trades_dir = Path("logs/trades")
    
    # We process historical files to populate the dashboard funnel,
    # but we yield to the event loop every 500 lines so we don't block the UI.

    while True:
        files = (glob.glob(str(logs_dir / "*.jsonl")) +
                 glob.glob(str(trades_dir / "*.jsonl")))
        state["debug"]["files_watched"] = len(files)
        for f in sorted(files):
            if f not in file_offsets:
                file_offsets[f] = 0 # New file, start from 0
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    fh.seek(file_offsets[f])
                    lines_processed = 0
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                process_event(json.loads(line))
                            except Exception:
                                pass
                        lines_processed += 1
                        if lines_processed % 500 == 0:
                            await asyncio.sleep(0)
                    file_offsets[f] = fh.tell()
            except Exception:
                pass
        await asyncio.sleep(0.5)


async def poll_engine():
    """Pull /status from Engine API, store only the fields we render."""
    engine_url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8080")
    while True:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{engine_url}/status", timeout=1.5)
                if resp.status_code == 200:
                    raw = resp.json()
                    port = raw.get("portfolio", {})
                    # positions is a list of open position objects in /status
                    positions_list = raw.get("positions") or []
                    if not isinstance(positions_list, list):
                        positions_list = []
                    open_count = len(positions_list)
                    state["engine"] = {
                        "equity":         port.get("equity"),
                        "realized_pnl":   port.get("realized_pnl"),
                        "exposure":       port.get("exposure"),
                        "cash":           port.get("cash") or port.get("available_balance"),
                        "unrealized_pnl": port.get("unrealized_pnl"),
                        "open_positions": open_count,
                        "bot_state":      raw.get("bot_state", "RUNNING"),
                        "uptime":         raw.get("uptime") or raw.get("trading_loop_uptime", ""),
                        "cycle_id":       raw.get("runtime_cycle_id", 0),
                        # Additional read-only stats — only populated if the engine
                        # already exposes them under any of these common keys.
                        # None of these are computed/derived here; if the engine
                        # doesn't report a value the field stays null and the UI
                        # shows an elegant "—" rather than fabricating a number.
                        "todays_pnl":     port.get("todays_pnl") or port.get("daily_pnl") or port.get("today_pnl"),
                        "drawdown":       port.get("drawdown") or port.get("max_drawdown") or port.get("current_drawdown"),
                        "total_trades":   raw.get("total_trades") or port.get("total_trades") or port.get("trade_count"),
                        "win_rate":       port.get("win_rate") or raw.get("win_rate"),
                        "profit_factor":  port.get("profit_factor") or raw.get("profit_factor"),
                    }
                    # Store full positions list for the SSE payload
                    # Field names from the engine DB schema: avg_entry, last_price, exposure, unrealized_pnl
                    state["positions"] = [
                        {
                            "symbol":        p.get("symbol", "?"),
                            "quantity":      p.get("quantity", 0),
                            "avg_entry":     p.get("avg_entry") or p.get("avg_entry_price") or p.get("fill_price") or p.get("entry_price", 0),
                            "mark_price":    p.get("last_price") or p.get("mark_price") or p.get("current_price") or p.get("avg_entry") or p.get("fill_price", 0),
                            "unrealized_pnl":p.get("unrealized_pnl", 0),
                            "exposure":      p.get("exposure") or p.get("position_value", 0),
                            "exposure_pct":  p.get("exposure_pct", 0),
                            "strategy":      p.get("strategy") or p.get("entry_strategy", ""),
                        }
                        for p in positions_list
                    ]
        except Exception:
            # Handle engine unavailable state cleanly
            state["engine"]["bot_state"] = "Waiting for engine..."
            
        await asyncio.sleep(1.0)


async def poll_diagnostics():
    """Refresh diagnostics block every 5 seconds.
    Uses native network checks instead of docker exec.
    """
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    pg_host = os.environ.get("POSTGRES_HOST", "localhost")
    pg_port = int(os.environ.get("POSTGRES_PORT", "5432"))

    loop = asyncio.get_event_loop()

    def _check_redis():
        try:
            r = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True, socket_timeout=2)
            val = r.get("quantfund:control")
            return True, val
        except Exception:
            return False, None

    def _check_postgres():
        try:
            with socket.create_connection((pg_host, pg_port), timeout=2):
                return True
        except Exception:
            return False

    while True:
        # 1. Redis control state + liveness (off-thread native python call)
        try:
            redis_ok, raw_ctrl = await loop.run_in_executor(None, _check_redis)
            state["diagnostics"]["redis_ok"] = redis_ok
            if raw_ctrl:
                try:
                    ctrl = json.loads(raw_ctrl)
                    state["diagnostics"]["control_state"] = {
                        "paused": bool(ctrl.get("paused")),
                        "reason": ctrl.get("reason", ""),
                    }
                except Exception:
                    pass
        except Exception:
            state["diagnostics"]["redis_ok"] = False

        # 2. Postgres liveness (off-thread native python socket test)
        try:
            pg_ok = await loop.run_in_executor(None, _check_postgres)
            state["diagnostics"]["postgres_ok"] = pg_ok
        except Exception:
            state["diagnostics"]["postgres_ok"] = False

        # 3. MEXC connectivity — call MEXC directly (NOT self-loop via localhost:8081)
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(
                    "https://api.mexc.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1"
                )
                data = resp.json()
                rows = data.get("value", data) if isinstance(data, dict) else data
                state["diagnostics"]["mexc_ok"] = (resp.status_code == 200 and len(rows) > 0)
        except Exception:
            state["diagnostics"]["mexc_ok"] = False

        # 4. Engine heartbeat age
        last_ts = state["diagnostics"]["last_log_wall_ts"]
        if last_ts is not None:
            state["diagnostics"]["engine_heartbeat_age_s"] = round(time.monotonic() - last_ts)

        await asyncio.sleep(5.0)


# â”€â”€â”€ Telegram helper (host-side, reads .env) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _send_telegram(message: str) -> bool:
    """
    Synchronous Telegram alert. Reads the same env vars as the engine's
    core/telegram_alerts.py.  Never raises to the caller.
    """
    try:
        enabled = str(os.getenv("ALERTS_ENABLED", "")).strip().lower()
        if enabled not in {"1", "true", "yes", "y", "on"}:
            print("[watchdog] Telegram skipped: ALERTS_ENABLED not set.", flush=True)
            return False

        token   = str(os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
        chat_id = str(os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
        if not token or not chat_id:
            print("[watchdog] Telegram skipped: missing BOT_TOKEN or CHAT_ID.", flush=True)
            return False

        payload = urlencode({
            "chat_id": chat_id,
            "text": str(message)[:3900],
            "disable_web_page_preview": "true",
        }).encode("utf-8")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = Request(
            url, data=payload, method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "QuantFundOS/1.0",
            },
        )
        with urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return '"ok":true' in body.lower()
    except Exception as exc:
        print(f"[watchdog] Telegram error: {exc}", flush=True)
        return False


# ————————————————————————————————————————————————————————————————————————————————————————————————————
async def watchdog():
    """
    Runs every 60 seconds. Compares four progress dimensions against the
    previous snapshot.

    A stall requires ALL FOUR to be simultaneously true:
      1. engine_heartbeat_age_s >= 60  (engine completely silent)
      2. cycle_id unchanged            (loop not advancing)
      3. candidates_processed unchanged (no new evaluations)
      4. total log-file bytes unchanged (files frozen on disk)

    If any single dimension shows movement the run is considered healthy.
    This avoids false positives during quiet market conditions where the
    heartbeat is fresh but candidate flow is temporarily low.

    Two consecutive stall ticks (≥ 2 consecutive minutes) → stalled=True
    and a Telegram alert is fired (throttled to once per 5 minutes).
    """
    # Give the engine 90 seconds to warm up before the first watchdog check.
    await asyncio.sleep(90)

    # Track wall-clock time of last successful engine HTTP ping (not log timestamps)
    last_engine_ok_ts: float = time.monotonic()

    while True:
        try:
            # --- Collect current snapshot ---
            logs_dir   = Path("logs/candidates")
            trades_dir = Path("logs/trades")
            all_files  = (
                glob.glob(str(logs_dir / "*.jsonl")) +
                glob.glob(str(trades_dir / "*.jsonl"))
            )
            log_size = sum(
                Path(f).stat().st_size for f in all_files if Path(f).exists()
            )

            # --- Condition 1: Engine HTTP reachability (primary heartbeat) ---
            engine_alive = False
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get("http://127.0.0.1:8080/status")
                    engine_alive = (r.status_code == 200)
            except Exception:
                engine_alive = False

            if engine_alive:
                last_engine_ok_ts = time.monotonic()

            # Update diagnostics with real HTTP-based heartbeat age
            state["diagnostics"]["engine_heartbeat_age_s"] = round(
                time.monotonic() - last_engine_ok_ts
            )

            snap = {
                "cycle_id":   state["engine"].get("cycle_id", 0),
                "candidates": state["funnel"]["ranked"],
                "log_size":   log_size,
                "hb_age":     state["diagnostics"]["engine_heartbeat_age_s"],
            }

            prev = state["watchdog"]["last_snapshot"]
            wd   = state["watchdog"]

            # --- Stall = engine unreachable AND no progress on any dimension ---
            hb_stale = state["diagnostics"]["engine_heartbeat_age_s"] >= 180

            if prev:
                cycle_frozen = (prev.get("cycle_id") == snap["cycle_id"])
                cands_frozen = (prev.get("candidates") == snap["candidates"])
                logs_frozen  = (prev.get("log_size") == snap["log_size"])
            else:
                cycle_frozen = cands_frozen = logs_frozen = False

            is_stall = (
                (not engine_alive) and
                hb_stale and
                cycle_frozen and
                cands_frozen and
                logs_frozen
            )
            # --- Update watchdog state ---
            if is_stall:
                wd["consecutive_stall_ticks"] += 1
                print(
                    f"[watchdog] Stall tick {wd['consecutive_stall_ticks']}/2 — "
                    f"hb_age={snap['hb_age']}s cycle={snap['cycle_id']} "
                    f"cands={snap['candidates']} log_bytes={snap['log_size']}",
                    flush=True,
                )

                if wd["consecutive_stall_ticks"] >= 2:
                    wd["stalled"] = True
                    now = time.time()
                    # Throttle Telegram to once per 5 minutes
                    if now - wd["last_alert_ts"] > 300:
                        wd["last_alert_ts"] = now
                        alert_msg = (
                            f"🚨 ENGINE STALLED — Mission Control\n"
                            f"No progress for {wd['consecutive_stall_ticks']} consecutive minutes.\n"
                            f"heartbeat_age={snap['hb_age']}s  "
                            f"cycle={snap['cycle_id']}  "
                            f"candidates={snap['candidates']}  "
                            f"log_bytes={snap['log_size']}\n"
                            f"Time: {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z"
                        )
                        # Run blocking HTTP in a thread so the event loop isn't blocked
                        await asyncio.get_event_loop().run_in_executor(
                            None, _send_telegram, alert_msg
                        )
                        print("[watchdog] STALLED â€” Telegram alert dispatched.", flush=True)
            else:
                if wd["stalled"]:
                    print("[watchdog] Stall cleared â€” engine resumed progress.", flush=True)
                wd["consecutive_stall_ticks"] = 0
                wd["stalled"] = False

            state["watchdog"]["last_snapshot"] = snap

        except Exception as exc:
            print(f"[watchdog] Unexpected error: {exc}", flush=True)

        await asyncio.sleep(60)


# â”€â”€â”€ SSE endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.get("/stream")
async def stream():
    """
    Yields one anonymous `data: {...}\\n\\n` blob per second.
    No named events, no request.is_disconnected() â€” both cause silent failures.
    Generator exits only when the client disconnects (GeneratorExit / conn reset).
    """
    async def generate():
        print("SSE: client connected", flush=True)
        try:
            tick = 0
            while True:
                wd = state["watchdog"]
                payload = {
                    "tick":        tick,
                    "funnel":      state["funnel"],
                    "rejections":  state["rejections"],
                    "leaderboard": sorted(
                        state["leaderboard"].values(),
                        key=lambda x: x.get("confidence", 0),
                        reverse=True,
                    )[:25],
                    "timeline":    state["timeline"][-30:],
                    "engine":      state["engine"],
                    "positions":   state["positions"],
                    "debug":       state["debug"],
                    "diagnostics": state["diagnostics"],
                    "server_ts":   time.time() * 1000,
                    "watchdog": {
                        "stalled":                 wd["stalled"],
                        "consecutive_stall_ticks": wd["consecutive_stall_ticks"],
                    },
                }
                yield f"data: {json.dumps(payload)}\n\n"
                tick += 1
                await asyncio.sleep(1.0)
        except (asyncio.CancelledError, GeneratorExit):
            log_info("SSE: client disconnected")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@app.get("/klines")
async def get_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 120):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"https://api.mexc.com/api/v3/klines"
                f"?symbol={symbol}&interval={interval}&limit={limit}"
            )
            data = resp.json()
            # MEXC wraps klines in {"value": [...], "Count": N} — unwrap to plain list
            if isinstance(data, dict) and "value" in data:
                return data["value"]
            return data
        except Exception:
            return []


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("mission_control.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/v2", response_class=HTMLResponse)
async def index_v2():
    with open("mission_control_v2.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    log_info("Starting AI Mission Control on http://0.0.0.0:8081")
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")

