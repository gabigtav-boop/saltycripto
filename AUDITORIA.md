# Auditoría del sistema (16 nov 2026)

> Auditoría hecha sobre la versión del Mac y trasladada a esta versión (nube) el mismo día. Aquí no hay WebSocket propio ni servidor local: el análisis corre en GitHub Actions y los precios en vivo llegan directamente de Kraken al navegador.

Revisión completa del bot del Mac: datos, servicio, histórico, servidor y proyección. Pruebas: `tests/test_auditoria.py` (11 nuevas; la suite completa pasa, la de paridad del motor v21 sin cambios).

## Calidad de los datos (solo lectura, sin hallazgos graves)
- Histórico profundo (Binance/Coinbase) y velas de Kraken: sin velas duplicadas ni desordenadas, sin OHLC incoherente, empalme sin saltos en la unión.
- Única rareza real: los primeros días de algunas monedas recientes en Kraken (p. ej. ZEC: 100000 → 880). Ahora la volatilidad es robusta frente a esas velas.

## Fallos encontrados y corregidos
| Zona | Problema | Corrección |
|---|---|---|
| `servicio.py` | Dos cargas completas podían correr a la vez (botón de actualizar + ciclo de 10 min) | Cerrojo `_main_lock`: la segunda se ignora |
| `servicio.py` | Un fallo puntual en una recarga silenciosa borraba la moneda de la pantalla | Se conservan los datos previos y se anota `recarga_fallo` |
| `servicio.py` | Precios de Coinbase/CoinGecko y derivados caducados seguían usándose para contrastar y para la previsión | Edad máxima (15 min precios, 30 min derivados); sin marca de edad se usa tal cual |
| `servicio.py` | La foto de ticker de la carga podía tener minutos | Si pasa de 20 s se vuelve a pedir ese par |
| `servicio.py` | `salud()` devolvía diccionarios compartidos con otros hilos (error al serializar) | Copia antes de enviar |
| `historico.py` | Varios hilos descargaban y escribían el mismo archivo; escritura no atómica | Cerrojo por archivo, `.tmp` + `replace`, recuerda `tope` (no hay más historia) |
| `servidor.py` | Errores de desconexión del navegador ensuciaban el registro; el flujo SSE moría ante un fallo | Bucle resistente con latido; se ignoran BrokenPipe/ConnectionReset |
| `proyeccion.py` | Una vela defectuosa inflaba la volatilidad durante cientos de velas | `limpiar()` + EMA robusta (inicio por mediana, tope relativo) |
| `proyeccion.py` | Monedas con poca actividad daban rangos con falsa precisión | Medida `activa` y aviso «Poca actividad» |
| `web/index.html` | Contexto (F&G, CoinGecko) caducado se mostraba como actual | Aviso de contexto antiguo |

## Comprobaciones en vivo
`verificar.py` (todo correcto), recarga manual repetida mientras se consulta `/api/state` (siempre ≥ 8 monedas con precio), 12 desconexiones bruscas del flujo SSE (websocket de Kraken sin reconectar), recorrido de BTC, SUI y PAXG en todos los plazos en el navegador sin errores de la página.

## Revisión del 17 nov 2026 (misma que en la versión del Mac)
| Zona | Hallazgo | Acción |
|---|---|---|
| `motor.validate` | El veredicto «ventaja modesta» salía de un número sin margen de error | `validacion.py`: intervalo por bloques de tiempo; el veredicto baja a «posible, no demostrada» si incluye el cero |
| `servidor.py` | Aceptaba cualquier `Host` (posible «rebinding» de DNS al probar en local) | Solo `localhost` y `127.0.0.1` |
| `indicadores.py` | RSI contrastado con los 19 valores publicados del ejemplo de Wilder | Prueba permanente en `tests/test_indicadores_clasicos.py` |
| `proyeccion.py`, `noticias.py`, tests | Variables e importaciones sin usar, comentario mezclado | Limpiado |
| `web/index.html` | Estética nueva, cono azul fuera, portada con esfera, modo claro real; el modo estático, la barra inferior y la lista móvil se conservaron | Comprobado a 375 y 1440 px, en oscuro y claro, con datos reales generados por `nube.py` |

## Revisión profunda del 17 nov 2026
| Zona | Hallazgo | Acción |
|---|---|---|
| `servicio.py`, web | Las horas salían en la zona del equipo (en GitHub, UTC: el estado decía «actualizado» con una hora de menos en verano) | `hora.py` y `Intl` con `Atlantic/Canary` en todo; noticias y agenda con su hora canaria |
| `noticias.py` | Fechas de noticias sin zona se leían en la hora del equipo | Se toman como UTC; prueba permanente |
| `fuentes.py`, `historico.py` | Una vela con precio 0 o NaN falseaba medias, RSI y zonas | `sanear()` en Kraken, Coinbase y el histórico profundo |
| Motor | Series extremas (constantes, saltos, precios diminutos, volumen 0) | Sin excepciones ni valores no finitos |
| Datos en vivo | RSI, ATR y EMA contra velas independientes de Coinbase | Coinciden (RSI ±0,1; ATR ±0,06; EMA20 ±0,01 %) |
| Rendimiento | Análisis en vivo: ~41 ms por segundo (8 monedas) | Sin cuello de botella que justifique tocar la traducción de la v21 |

## Límites que siguen vigentes
- La proyección no tiene dirección: ningún método probado la acierta mejor que el azar (`ANALISIS_PREDICCION.md`).
- La «Lectura de los datos» usa pesos fijos sin validar (se llama «acuerdo»).
- Los errores `ERR_CONNECTION_REFUSED` en la consola del navegador de pruebas son de los reinicios del servidor de pruebas, no de la aplicación.
