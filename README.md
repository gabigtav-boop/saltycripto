# Saltycripto · versión nube (en desarrollo)

> Copia de trabajo para la versión que se publicará en GitHub y se verá desde el iPhone.
> La versión estable del Mac está en `~/Desktop/claude` y no se toca desde aquí. Esta copia usa el puerto **8502** (la del Mac, el 8501), así que pueden funcionar a la vez.

Plataforma de análisis de mercado cripto en tiempo real, en Python. Solo analiza: **no opera ni es asesoramiento financiero**.

## Abrir
Doble clic en **Saltycripto** (en el Escritorio). Arranca el motor en segundo plano si no está en marcha y abre el navegador en http://localhost:8501.
La primera vez instala lo necesario (requiere Python 3 de python.org).

- Detener el motor: `./abrir.sh --parar`
- Registro del motor: `datos/servidor.log`

## Qué hace
| Capa | Frecuencia | Fuente |
|---|---|---|
| Precio, libro de órdenes (verificado con CRC32), operaciones, velas de 1 min | al instante (WebSocket) | Kraken |
| Análisis técnico en vivo (vela en formación), zonas, semáforo, fiabilidad, microestructura | cada segundo | motor propio |
| Motor completo v21: 16 señales en 5 familias, 4 temporalidades, histórico sin información futura, validación | cada 10 min | Kraken, Coinbase, CoinGecko |
| Derivados: funding, % de cuentas en largo, interés abierto | cada minuto | Binance Futures (o Bybit / OKX si no responde) + Kraken Futures |
| Noticias e impacto fundamental (temas, activos afectados, reacción real del precio) | cada minuto | 14 medios (CoinDesk, Cointelegraph, The Block, Reserva Federal, CNBC…) |
| Agenda macro (IPC, Fed, empleo…) | cada hora | ForexFactory |
| Sentimiento compuesto 0-100 | cada minuto | miedo y codicia, impulso, amplitud técnica, funding, posicionamiento, noticias |

Versión publicada (GitHub Pages): el análisis se renueva cada 20-30 min; el botón ↻ vuelve a descargar el último análisis saltándose cachés, reconecta los precios en vivo y dice de cuándo es el análisis y cuándo llega el siguiente. En el Estudio, los **casos parecidos** van numerados en el gráfico (al pasar el cursor o tocar uno en la lista se resalta su camino; un clic lo fija) y el detalle técnico, de derivados y de fiabilidad va plegado. **Mi posición** acepta monedas, invertido o precio de compra (dos de los tres bastan) y números en formato español (3.200 o 1.500,25).

Secciones: **Mercado** (panorama, sentimiento, tabla de mercados con previsión, noticias, agenda, derivados y si el sistema funciona), **Estudio** (cada moneda a fondo: gráfico, previsión combinada con objetivo e invalidación, niveles, noticias que la afectan, resumen del libro y del flujo, análisis técnico, derivados, fiabilidad y tu posición), **Noticias** (efecto conjunto sobre el gráfico de cada activo y lista desplegable) y **Datos** (estado y verificación de todas las fuentes).

Flecha del gráfico (Estudio): **pronóstico por analogías**. Cada vela del histórico (años de velas de Binance/Coinbase empalmadas con Kraken, guardadas en `datos/historico/`) se describe con 14 rasgos sin escala; para el estado actual se buscan los k momentos pasados más parecidos y lo que pasó después, reescalado a la volatilidad actual, da el recorrido central, el abanico de percentiles (10-25-50-75-90) y la probabilidad de subida. Antes de fiarse, el método se valida fuera de muestra (acierto, cobertura real del cono, destreza frente a no saber nada) y el cono se ajusta para cubrir lo que promete. Las analogías solo pesan si demostraron destreza; si no, la flecha muestra el rango probable y solo se inclina, de forma acotada, por noticias, derivados, flujo y sentimiento, y lo dice en pantalla. Detalle en `ANALISIS_BOTS.md`.

Avisos (campana): semáforo, previsión, entradas y salidas, movimientos bruscos, noticias de impacto alto y eventos macro. Se comprueban cada segundo; los cambios de señal deben mantenerse unos segundos para evitar falsas alarmas. La vista anterior sigue en `/clasica`.

## Rendimiento
- El canal en vivo envía cada segundo solo lo que ha cambiado (≈22 KB/s; la proyección completa solo de la moneda y temporalidad abiertas).
- El estado completo y la web viajan comprimidos (gzip): 436 → 155 KB.
- La lectura técnica de cada vela cerrada se guarda en `datos/historico/s21_*.json`: la calibración de la flecha es ~12× más rápida y el arranque baja de ~60 s a ~40 s.
- Las velas de 1 minuto se rellenan en los minutos sin operaciones, para que el momento y la volatilidad se midan sobre minutos reales.
- `datos/servidor.log` rota al superar 2 MB.

## Verificación
- `.venv/bin/python -m pytest` — pruebas automáticas, entre ellas la **paridad exacta con la v21**: el motor Python da los mismos resultados que el JavaScript original sobre datos reales (necesita Node.js).
- `.venv/bin/python verificar.py` — con la app en marcha, compara lo que muestra con consultas independientes a Kraken, Coinbase y Binance.

## Honestidad del análisis
La pestaña *¿Funciona el sistema?* muestra el histórico real: la lectura técnica general tiene una ventaja modesta, pero las entradas y salidas automáticas **no han demostrado beneficio** descontando costes. Úsalas como mapa de niveles, no como órdenes.
El análisis de noticias es por reglas transparentes (cada noticia muestra las palabras que la clasificaron) y se contrasta con la reacción real del precio.

## Estructura
- `cripto/motor.py`, `cripto/indicadores.py` — motor técnico v21 (traducción verificada)
- `cripto/envivo.py` — WebSocket de Kraken
- `cripto/noticias.py` — analizador fundamental y agenda
- `cripto/sentimiento.py` — derivados y sentimiento compuesto
- `cripto/prevision.py` — previsión combinada por moneda · `cripto/proyeccion.py` — flecha por analogías · `cripto/historico.py` — histórico profundo
- `cripto/servicio.py` — orquestación en tiempo real · `servidor.py` — servidor local
- `web/index.html` — interfaz · `web/clasica.html` — vista v21
- `legacy/` — versión anterior (Streamlit)
