#!/usr/bin/env bash
set -euo pipefail
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it before live trading."
fi
docker compose up -d --build
docker compose ps
