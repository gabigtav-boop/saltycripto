# saltycripto-nube · versión publicada en GitHub

## REGLA PRINCIPAL: dos proyectos separados que nunca deben solaparse

| | **Este proyecto: saltycripto-nube (GitHub)** | Otro proyecto: Saltycripto (Mac) |
|---|---|---|
| Carpeta | `~/Desktop/saltycripto-nube` | `~/Desktop/claude` |
| Uso | Web publicada en GitHub Pages para verla en el iPhone | Versión estable que el usuario usa en su Mac |
| Arranque | GitHub Actions (cron cada 10 min; en la práctica 30-50 min, hasta 2 h de madrugada) (`.github/workflows/publicar.yml` → `nube.py`) | Acceso directo `Saltycripto.app` → `abrir.sh` |
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
- Binance bloquea los servidores de GitHub (EE. UU.): `SALTY_SIN_BINANCE=1` → histórico desde Coinbase y derivados por orden Bybit → OKX → Kraken Futures (`cripto/sentimiento.py`). Kraken no publica cuentas en largo ni OI de 24 h: la web oculta esas columnas si ninguna fuente las aporta.
- Interfaz (`web/index.html`): en modo estático el botón ↻ (`$('rf')`) descarga `datos/estado.json` con `no-store`, reconecta el WebSocket y avisa con una burbuja; el sondeo de cada minuto usa `no-cache` (304 si no hay análisis nuevo). Los casos parecidos van numerados en el gráfico (`projDraw`, `CASEHOV`/`CASEPIN`); los desplegables `det()` recuerdan su estado en `OPD`; `memoH` evita repintar HTML idéntico.
- Repintado: `memoH`/`morphInto` actualizan el DOM en el sitio (no reconstruyen): así no se pierden el desplazamiento horizontal, los toques ni los desplegables aunque el HTML cambie cada segundo. Datos usa `APP.innerHTML`. Las tiras o tablas con desplazamiento horizontal deben vivir en contenedores que no se vacíen.
- Pruebas en el navegador de la app: el panel oculto pausa `requestAnimationFrame` (hay que llamar a `projDraw()` a mano) y no anima las burbujas; las tareas largas de `javascript_tool` expiran a los 45 s (dividir por monedas).
- Sin dirección: la proyección (`cripto/proyeccion.py`) es un **rango por volatilidad con probabilidad de tocar niveles** más **5 casos reales representativos** (de 50 buscados por volatilidad y tendencia, a los percentiles 10-25-50-75-90, sin nube). Los casos no predicen nada: no presentarlos como señal. No reintroducir dirección sin pasar el protocolo de `ANALISIS_PREDICCION.md`. La web llama «acuerdo» (no «confianza») a la coincidencia entre los 7 factores. Los tickers SOL/HYPE/SUI solo cuentan en mayúsculas (noticias.py). `tests/test_nube.py` cubre la cadena de derivados, las noticias y la calculadora.
- Modo estático: `nube.py` guarda en `moneda/<S>.json` el abanico, los casos y las curvas `F_up`/`F_dn`; `escP()` (web/index.html) reescala el abanico al precio en vivo y recalcula la probabilidad de cada nivel. Un solo botón de reiniciar vista (cabecera); `#chart` lleva `contain:size`: no quitarlo.
- Auditoría 16 nov 2026 (`AUDITORIA.md`, `tests/test_auditoria.py`): una sola carga completa a la vez, recargas que no pierden la moneda, fuentes externas con edad máxima, histórico con cerrojo y escritura atómica, volatilidad robusta a velas defectuosas, aviso de poca actividad.
- Calculadora de posición (`num`, `pq`, `pinv`, `posCalc`): con dos de los tres datos (monedas, invertido, precio de compra) se deduce el tercero; `num` entiende formato español (3.200 = tres mil doscientos; 0.05 = decimal).
- La web vive en `/saltycripto/`: las rutas de recursos deben ser **relativas** (`vendor/…`, no `/vendor/…`).
- Motor técnico v21 con prueba de paridad: no tocar `cripto/motor.py` ni `cripto/indicadores.py` sin actualizar `tests/test_paridad.py`.

## Si la carpeta saltycripto-nube se mueve dentro de ~/Desktop/claude
Siguen siendo **proyectos separados** aunque uno quede dentro del otro:
- Recrear el `.venv` de saltycripto-nube (tiene grabada la ruta antigua): `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest`.
- Actualizar las rutas de esta tabla en los dos `CLAUDE.md`.
- En el proyecto del Mac, cualquier búsqueda, copia o limpieza recursiva (`grep -r`, `find`, `rsync`, `rm`) debe **excluir** la subcarpeta `saltycripto-nube/`.
- No hacer nunca `git init` en `~/Desktop/claude`: el repositorio de saltycripto-nube quedaría dentro de otro.
- GitHub y la web publicada no se ven afectados por mover la carpeta.
