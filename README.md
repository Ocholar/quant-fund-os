# Quant Fund OS

Paper-first autonomous quant research and execution scaffold for Oracle Cloud Free Tier.

## Safety defaults

- `LIVE_TRADING=false`
- `REQUIRE_HUMAN_APPROVAL=true`
- paper market data and paper execution only
- risk engine blocks live autonomous execution by default

This is infrastructure and research tooling, not a guaranteed profit system.

## Local run

```bash
cp .env.example .env
docker compose up -d --build
```

Open:

- API: http://localhost:8000
- Trades: http://localhost:8000/trades
- Portfolio: http://localhost:8000/portfolio
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

## Oracle Cloud VM run

```bash
chmod +x scripts/oracle_bootstrap.sh scripts/deploy_oracle.sh
./scripts/oracle_bootstrap.sh
# log out, SSH back in
./scripts/deploy_oracle.sh
```

## Components

- `ai/`: autonomous agent, strategy evolution, online learner, allocator
- `core/`: risk engine, metrics, portfolio, regime detection
- `data/`: paper market data and feature store
- `execution/`: synthetic Level 2 order book, slippage, latency, market impact
- `research/`: backtest, walk-forward, Monte Carlo, stress testing
- `services/`: FastAPI dashboard API and Prometheus metrics
- `infra/`: Prometheus, Grafana, Terraform OCI scaffold

## Production warning

Do not add real exchange keys until paper trading, metrics, logs, and kill-switch behavior are verified.
