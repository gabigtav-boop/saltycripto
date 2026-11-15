# saltycripto-nube · versión publicada en GitHub

## REGLA PRINCIPAL: dos proyectos separados que nunca deben solaparse

| | **Este proyecto: saltycripto-nube (GitHub)** | Otro proyecto: Saltycripto (Mac) |
|---|---|---|
| Carpeta | `~/Desktop/saltycripto-nube` | `~/Desktop/claude` |
| Uso | Web publicada en GitHub Pages para verla en el iPhone | Versión estable que el usuario usa en su Mac |
| Arranque | GitHub Actions cada 20 min (`.github/workflows/publicar.yml` → `nube.py`) | Acceso directo `Saltycripto.app` → `abrir.sh` |
| Puerto local (pruebas) | **8502** | 8501 |
| Entorno Python | `.venv` propio | `.venv` propio |
| Git / GitHub | Repositorio público `gabigtav-boop/saltycripto` (rama `main`) | Ninguno |

Normas para Claude:
- **No modificar, copiar, sincronizar ni ejecutar nada de `~/Desktop/claude` desde aquí**, y no llevar allí cambios de este proyecto, salvo que el usuario lo pida de forma explícita para ese caso concreto.
- Un cambio hecho en un proyecto **no** se aplica al otro. Si el usuario quiere la misma mejora en los dos, se hace por separado y se dice en qué carpeta se hizo cada una.
- Si el usuario habla de "mi versión del Mac", "el acceso directo" o "la que uso en casa", se trabaja en `~/Desktop/claude`, no aquí. Si no está claro a cuál se refiere, **preguntar antes de tocar nada**.
- **Cada `git push` a `main` publica la web** para el usuario: probar en local antes (`SALTY_SIN_BINANCE=1 .venv/bin/python nube.py` y servir `publico/`).
- El repositorio es público: no añadir datos personales, rutas absolutas del Mac, correos ni claves. Los commits usan el correo privado `…@users.noreply.github.com`.

## Notas técnicas
- `nube.py` ejecuta el motor una vez y genera `publico/` (web estática + `datos/estado.json` + `datos/moneda/<SÍMBOLO>.json`).
- La web publicada funciona con `window.ESTATICO=true`: lee los archivos y se conecta directamente al WebSocket de Kraken para precios, velas, libro y operaciones.
- Binance bloquea los servidores de GitHub (EE. UU.): `SALTY_SIN_BINANCE=1` → derivados desde Kraken Futures e histórico desde Coinbase.
- La web vive en `/saltycripto/`: las rutas de recursos deben ser **relativas** (`vendor/…`, no `/vendor/…`).
- Motor técnico v21 con prueba de paridad: no tocar `cripto/motor.py` ni `cripto/indicadores.py` sin actualizar `tests/test_paridad.py`.
