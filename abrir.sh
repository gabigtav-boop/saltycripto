#!/bin/bash
# Arranca Saltycripto en segundo plano (si no está ya en marcha) y lo abre en el navegador.
#   ./abrir.sh           → arrancar y abrir
#   ./abrir.sh --parar   → detener el motor
cd "$(dirname "$0")" || exit 1
URL="http://localhost:8502"
PID=datos/servidor.pid
mkdir -p datos

if [ "$1" = "--parar" ]; then
  [ -f "$PID" ] && kill "$(cat "$PID")" 2>/dev/null && rm -f "$PID" && echo "Saltycripto detenido" || echo "No estaba en marcha"
  exit 0
fi

if ! curl -s -m 2 "$URL/api/health" >/dev/null; then
  if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv || { osascript -e 'display alert "Saltycripto" message "Falta Python 3. Instálalo desde python.org y vuelve a intentarlo."'; exit 1; }
  fi
  # instalar dependencias solo si cambiaron
  H=$(shasum requirements.txt | cut -d' ' -f1)
  if [ "$(cat datos/req.sha 2>/dev/null)" != "$H" ]; then
    .venv/bin/pip install -q -r requirements.txt >> datos/servidor.log 2>&1 && echo "$H" > datos/req.sha
  fi
  # el registro no crece sin límite: si pasa de 2 MB se guarda como .old y se empieza uno nuevo
  if [ -f datos/servidor.log ] && [ "$(stat -f%z datos/servidor.log)" -gt 2000000 ]; then
    mv -f datos/servidor.log datos/servidor.log.old
  fi
  nohup .venv/bin/python servidor.py --no-open >> datos/servidor.log 2>&1 &
  echo $! > "$PID"
  for _ in $(seq 1 60); do curl -s -m 1 "$URL/api/health" >/dev/null && break; sleep 0.5; done
fi
open "$URL"
