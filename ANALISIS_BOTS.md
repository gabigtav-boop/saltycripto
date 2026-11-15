# Análisis de bots cripto y qué hemos aplicado a Saltycripto

Fecha: 15 de noviembre de 2026. Saltycripto es una plataforma de **análisis** (no opera). Por eso lo que nos interesa de los bots del mercado no es su ejecución de órdenes, sino **cómo leen los datos, cómo validan sus señales y cómo evitan engañarse**.

## 1. Tipos de bots que existen

| Tipo | Qué hace | Cuándo funciona | Ejemplos | Qué nos sirve |
|---|---|---|---|---|
| **Grid (rejilla)** | Coloca órdenes de compra y venta escalonadas en un rango y gana con la oscilación | Mercado lateral; pierde en tendencias fuertes | Pionex, Bitsgap, 3Commas, KuCoin | Necesita un rango bien definido: nuestras zonas por confluencia y el cono de la flecha son justo ese "rango probable" |
| **DCA (compra periódica)** | Compra cantidades fijas cada cierto tiempo o en caídas | Acumulación a largo plazo | 3Commas, Cryptohopper | Poco: no hay lectura de mercado, solo disciplina |
| **Arbitraje** | Explota diferencias de precio entre mercados o entre spot y futuros | Siempre que la diferencia supere las comisiones; margen muy fino | Hummingbot | La comparación Kraken/Coinbase/CoinGecko que ya hacemos es la base de la detección de precios anómalos |
| **Market making** | Cotiza compra y venta a la vez y gana el spread, gestionando el inventario | Mercados líquidos; riesgo de quedarse con inventario en tendencia | Hummingbot (modelo Avellaneda-Stoikov) | Su gran idea: **el precio de referencia y el ancho de las bandas dependen de la volatilidad reciente**, no de una cifra fija. Aplicado al cono de la flecha |
| **Señales / tendencia** | Indicadores técnicos (medias, RSI, MACD, bandas) con reglas de entrada y salida | Tendencias claras; falla en lateral | Freqtrade, Jesse, OctoBot, Cryptohopper, TradingView+bots | Es lo que ya tenemos (motor v21, 16 señales). Lo que aprendemos de ellos es la **validación**: backtest sin información futura, *walk-forward*, protecciones |
| **Aprendizaje automático** | Modelos entrenados y reentrenados sobre rasgos del mercado | Solo con validación muy estricta; se sobreajustan con facilidad | FreqAI (Freqtrade), TensorTrade | Su flujo de trabajo (rasgos sin escala, ventana de entrenamiento, validación fuera de muestra, reentrenamiento periódico) es el que hemos copiado para la flecha |
| **Flujo de órdenes / microestructura** | Desequilibrio del libro, delta de volumen (CVD), intensidad de operaciones | Horizontes de segundos a minutos | Bots propietarios, Buildix, Hyperliquid analytics | Es nuestra capa de microestructura (libro CRC32, flujo 1/5/15 min, muros). La evidencia académica: el desequilibrio del libro tiene poder predictivo **real pero pequeño** (IC 0,01-0,05) |
| **Sentimiento / noticias** | Clasifica noticias y redes y las convierte en sesgo | Eventos de alto impacto | Varios bots "AI" comerciales | Nuestro analizador por reglas transparentes + reacción real del precio ya hace esto sin caja negra |
| **Copy trading** | Replica a otros traders | Depende por completo del copiado | Bybit, Bitget, Zignaly | Nada |

Conclusión del sector: ninguna reseña independiente demuestra que un bot comercial sea rentable de forma sostenida; las clasificaciones son en su mayoría publicidad de afiliados. Lo que sí está contrastado es **el método**: datos limpios, señales validadas fuera de muestra y gestión del riesgo por volatilidad.

## 2. Los mejores bots abiertos y qué hacen bien

- **Freqtrade** (Python, más de 50.000 estrellas). Lo mejor: *dry-run* obligatorio, backtesting con velas de varias temporalidades ("informative pairs"), hyperopt con funciones de pérdida (Sharpe, drawdown, Calmar), *protections* (parar tras rachas de pérdidas, enfriamiento), y **FreqAI**: reentrena un modelo ligero sobre rasgos recientes y lo usa como una señal más. Su lección: **toda señal se mide contra el futuro real sin trampas y se reentrena con el tiempo**.
- **Hummingbot** (market making y arbitraje, 40+ mercados). Su estrategia Avellaneda-Stoikov desplaza el precio de referencia según el inventario y **ensancha los márgenes con la volatilidad medida en las últimas N operaciones**. Lección: la anchura de cualquier banda debe escalar con la volatilidad actual.
- **Jesse** (Python, investigación). Backtests limpios, rasgos precalculados, separación estricta entre investigar y operar.
- **OctoBot / NautilusTrader**. Arquitectura por eventos, cada fuente de datos verificada e independiente, reconexión automática. Es lo que hace nuestro `envivo.py` (WebSocket con CRC32 del libro y reconexión exponencial).

## 3. Qué hemos cambiado en Saltycripto (esta versión)

### 3.1 Histórico profundo (`cripto/historico.py`)
Kraken solo da 720 velas por temporalidad (30 días en 1 h). Ahora se descargan **4.000 velas de 1 h, 3.000 de 4 h, 1.600 diarias y 3.000 de 1 min** de Binance (en euros; en USDT solo para HYPE, ZEC y PAXG, que no cotizan en euros allí) con Coinbase como respaldo, se guardan en `datos/historico/` y se actualizan de forma incremental. Las velas antiguas se **empalman con las de Kraken ajustando la escala en la unión**, como hacen los proveedores de datos; todos los cálculos de la flecha son relativos (retornos, distancias en ATR, ratios), así que la serie es coherente.

### 3.2 La flecha: pronóstico por analogías (`cripto/proyeccion.py`)
Antes: media y desviación de los movimientos de las últimas 720 velas, agrupadas en tres estados (alcista/neutral/bajista), con una campana normal. Problemas: 30 días de histórico en 1 h, muestras solapadas contadas como independientes, deriva del último mes disfrazada de predicción y bandas simétricas que no sabían si cubrían lo que prometían.

Ahora (método de *análogos* o vecinos más cercanos, el mismo que FreqAI permite usar y que la meteorología usa desde hace décadas):
1. **14 rasgos sin escala** por vela: lectura técnica v21, RSI, distancia a EMA20/50/200 en ATR, impulso normalizado por volatilidad (dos plazos), Bollinger %B, régimen de volatilidad (corta/larga), ADX, volumen relativo, posición en el rango de 50 velas y hora del día (en 1 m y 1 h).
2. **Resultado real normalizado**: cuánto se movió el precio en las H velas siguientes dividido por la volatilidad (EWMA) de ese momento. Así un día tranquilo y uno agitado se comparan en igualdad y la proyección se reescala a la volatilidad actual (la lección de Hummingbot).
3. **k vecinos ponderados** por parecido, con los rasgos pesados por su correlación con el resultado y **sin contar dos veces momentos solapados**.
4. **Validación fuera de muestra en dos tramos** sobre el último 35 % del histórico, cada punto solo con información anterior: en el primer tramo se mide cuánto hay que ensanchar el cono para que cubra lo que promete (**ajuste conformal**); en el segundo, con el cono ya ajustado, se mide acierto direccional, cobertura real de las bandas 50 % y 80 % y **destreza** (pérdida *pinball* frente a la distribución de siempre sin deriva).
5. **Las analogías solo pesan en la medida en que demostraron destreza**. Si no la demostraron, la flecha dibuja el rango probable (mediana cero, cono calibrado) y solo se inclina, de forma acotada (±0,35 σ), por noticias, derivados, flujo y sentimiento. Y lo dice en pantalla.

Resultado honesto de la validación (15/11/2026, BTC/ETH/SOL, 1 h a 1 sem): la cobertura real del cono queda cerca de lo prometido (banda 80 %: 71-88 %; banda 50 %: 35-64 %), pero **ninguna temporalidad muestra destreza direccional fiable fuera de muestra** (los rasgos técnicos aprendidos en el mercado alcista de 2024-2025 predicen al revés en 2026). Esto coincide con la literatura: a horizontes de horas y días la dirección es casi impredecible con análisis técnico; lo que sí se puede estimar bien es **el tamaño probable del movimiento**. La flecha nueva es más certera precisamente porque no afirma lo que no puede demostrar y acierta en lo que sí puede: el rango.

### 3.3 Más visual (`web/index.html`)
Abanico de percentiles 10-25-50-75-90 con la forma real de los recorridos (no una campana), caminos en gris de los cinco momentos pasados más parecidos, flecha central, marca del horizonte, etiquetas del rango al final, nivel que puede frenar, chip con la probabilidad de subida y el acierto fuera de muestra, y una tarjeta con veredicto en lenguaje claro, cobertura real del cono, casos parecidos (con fecha y qué pasó después) y rasgos que más pesan.

### 3.4 Errores corregidos y optimizaciones
- **`RuntimeError: deque mutated during iteration`** (250 veces en el registro): el hilo del WebSocket añadía mensajes mientras el servidor los contaba para la salud. Ahora ambas colas se tocan bajo el cerrojo.
- **Trazas por desconexiones del navegador** (52 `ConnectionResetError`): el servidor las trataba como errores. Se silencian solo esas.
- **SSE**: condición sin efecto (`or True`) eliminada; un envío por segundo con latido.
- **Carga de red**: la proyección completa (recorridos y análogos) solo se envía para la moneda abierta; para el resto va un resumen compacto.
- **Caché de calibración** por última vela cerrada (antes acumulaba claves sin límite): una entrada por moneda y temporalidad; la de 1 minuto se recalcula cada 5 minutos, el resto cuando cierra una vela.
- La flecha de 1 minuto se calibra con velas de 1 minuto reales (antes era la de 1 h reescalada).

## 4. Lo que no hemos hecho (a propósito) y siguientes pasos razonables
- **No operamos.** Convertir esto en un bot ejecutor exige gestión de riesgo (tamaño por volatilidad, stop por ATR, límite de pérdida diaria), *dry-run* largo y claves API con permisos mínimos. Es un proyecto aparte.
- **Siguiente mejora con sentido**: medir el flujo de órdenes de forma continua (CVD y desequilibrio del libro por minuto, guardados en disco) para validar si en 1 m y 1 h aportan destreza; la evidencia dice que es pequeña pero real.
- **Protecciones al estilo Freqtrade** para los avisos: silenciar avisos de una moneda tras varias señales fallidas seguidas.

## Fuentes consultadas
- Tipos de bots y plataformas: [Bitsgap](https://bitsgap.com/blog/best-crypto-trading-bots-in-2026-strategies-types-and-how-to-choose), [Zignaly](https://zignaly.com/crypto-trading/bots/types-of-crypto-trading-bots), [Koinly](https://koinly.io/blog/best-crypto-trading-bots/), [CoinGape](https://coingape.com/best-crypto-trading-bots), [edgeX](https://pro.edgex.exchange/en-US/news/article/ai-crypto-trading-bot-review-2026), [Gainium (código abierto)](https://gainium.io/best/open-source)
- Freqtrade / FreqAI: [documentación de hyperopt](https://codeclimate.com/github/freqtrade/freqtrade/docs/hyperopt.md/source), [reseña Gainium](https://gainium.io/review/freqtrade), [FreqAI con técnicas institucionales](https://dev.to/stefano_viana_dda882cfae5/how-i-built-a-profitable-freqai-plugin-using-institutional-ml-techniques-1l9k)
- Hummingbot / Avellaneda-Stoikov: [guía](https://hummingbot.org/blog/guide-to-the-avellaneda--stoikov-strategy/), [detalle técnico](https://hummingbot.org/blog/technical-deep-dive-into-the-avellaneda--stoikov-strategy/), [documentación](https://docs.hummingbot.org/strategies/avellaneda-market-making)
- Flujo de órdenes: [Trading Strategy — Order flow](https://tradingstrategy.ai/docs/learn/order-flow.html), [Crypto Orderflow Alpha Report](https://delphicalpha.substack.com/p/crypto-orderflow-alpha-report-feb), [arXiv 2602.00776](https://arxiv.org/pdf/2602.00776)
- Pronóstico probabilístico (cuantiles, pérdida pinball) en cripto: [Dudek, Orzeszko y Fiszeder 2025](https://arxiv.org/html/2508.15922v1), [Dudek, Kasprzyk y Pełka 2025](https://arxiv.org/abs/2511.20105v1)
- Límite de 720 velas de Kraken: [referencia OHLC](https://docs.kraken.com/api-reference/market-data/get-ohlc-data.md), [datos históricos](https://docs.kraken.com/exchange/guides/general/historical-data)
