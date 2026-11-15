#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; fi
DEMO=1 .venv/bin/streamlit run app.py
