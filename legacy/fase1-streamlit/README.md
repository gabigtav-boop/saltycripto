# Cripto Intel (Python) · fase 1
Panel en directo con precios de Kraken en EUR (BTC, ETH, XRP, SOL, HYPE, SUI, ZEC, ORO=PAXG) y gráfico de velas con EMA20/50, RSI y ATR. Solo análisis, no opera.

## Ejecutar (Mac)
1. Instala Python 3.10+ (python.org) y VS Code.
2. Abre esta carpeta en VS Code, terminal (Ctrl+`) y escribe:
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/streamlit run app.py
3. Se abre el navegador solo. Sin internet: `DEMO=1 .venv/bin/streamlit run app.py`.
(También puedes hacer doble clic en `iniciar.command`; si macOS lo bloquea: clic derecho > Abrir.)

## Tests
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest

## Nota
El WebSocket de Kraken no se ha podido probar desde el entorno de desarrollo (sin acceso a Kraken). Si "Estado" muestra "respaldo REST", funciona igual con precio cada 5 s.
