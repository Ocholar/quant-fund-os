# Quant Fund OS: Institutional Grade Autonomous Research v2.5

![Institutional Dashboard](https://github.com/Ocholar/quant-fund-os/raw/main/assets/preview.png)

An "Ultra-Modern" quantitative research tool designed for 24/7 autonomous paper trading across a universe of 60+ liquid USDT pairs on MEXC. Featuring evolutionary strategy scoring, safety circuit breakers, and institutional-grade risk management.

## 🚀 Key Features

- **Top-60 Universe**: Automatically monitors the most liquid MEXC pairs for multi-symbol arbitrage and momentum.
- **Evolutionary AI**: Over 10,000 strategy variations compete; the system automatically promotes top performers and blocks underperforming "Alpha" agents.
- **GMT+3 Native**: Fully configured for Kenyan Time for intuitive trade reporting and dashboard monitoring.
- **Institutional Safety**:
  - **Daily Loss Guard**: Auto-pauses all fleet activity if drawdown exceeds set limits.
  - **Liquidity Circuit Breaker**: Detects exchange execution errors and halts trading to prevent fat-finger scenarios.
  - **Dynamic Symbol Quarantine**: Automatically blocks symbols that exhibit toxic behavior or consistent slippage losses.
- **Glassmorphic Dashboard**: A premium, high-frequency HUD for real-time portfolio tracking with neon visual cues for system state.

## 🛠 Tech Stack

- **Core Engine**: Python 3.10+ (Async-ready core)
- **Execution**: CCXT (MEXC Native Integration)
- **Intelligence**: Custom Autonomous Fund Agent with Bayesian strategy scoring.
- **Database**: High-concurrency SQLite with real-time portfolio snapshots.
- **Dashboard**: FastAPI + Modern Glassmorphic HTML5/CSS3.

## 🚦 Getting Started

### Prerequisites

- Python 3.10+
- MEXC API Keys (Read-only for Paper, Trade for Live)

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/Ocholar/quant-fund-os.git
   cd quant-fund-os
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure your environment:

   ```bash
   cp .env.example .env
   # Edit .env with your keys and GMT+3 settings
   ```

### Execution

Launch the Hub and the Fleet concurrently:

```bash
# Terminal 1: The Research Bot
python main.py

# Terminal 2: The Institutional HUD
python -m uvicorn services.api:app --host 127.0.0.1 --port 8002
```

Access the dashboard at `http://localhost:8002/dashboard`.

## 🛡 Risk Management

The OS is hardcoded for **Safety First**. It includes a "Kill Switch" for manual emergency halts and automated "Risk-Off" regime detection that liquidates exposure during market volatility.

## 📊 Dashboard Overview

- **Net Equity**: Real-time value accounting for mark-to-market positions.
- **Alpha Probability**: Win-rate estimates derived from the current strategy mix.
- **Evolutionary Scores**: Audit of which "Evo" agents are currently winning.

---
*Created for Quantitative Research Purposes. Use with Caution.*
