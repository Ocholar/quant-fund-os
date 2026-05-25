#!/bin/bash
# Initialize the database
python init_full_sqlite.py

# Start the Uvicorn API server in the background
python -m uvicorn services.api:app --host 0.0.0.0 --port 8080 &

# Start the main quantitative research bot in the foreground
python main.py
