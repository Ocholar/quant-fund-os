#!/bin/bash
# Start the Uvicorn API server in the background
python -m uvicorn services.api:app --host 0.0.0.0 --port 8000 &

# Start the main quantitative research bot in the foreground
python main.py
