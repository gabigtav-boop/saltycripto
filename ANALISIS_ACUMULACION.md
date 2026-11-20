# ¿Acumular en las caídas fuertes mejora a la compra periódica? (estudio 11, 19 nov 2026)

Reproducir: `.venv/bin/python estudios/bajar11.py` (≈ 1 min, datos públicos) y `.venv/bin/python estudios/estudio11_acumulacion.py`
(≈ 12 s, semilla fija). Resultados completos: `estudios/resultados_estudio11.json` (la web lee de ahí el veredicto).

## Qué se probó
La señal es `cripto/acumulacion.py`, **la misma función que se ve en vivo**. Umbrales de manual fijados antes de mirar nada:

| Condición | Regla (vela diaria cerrada) |
|---|---|
| Caída fuerte (obligatoria) | condición literal de `acum-1`: `1 − C/M ≥ 6·A/C`, con cierre C, máximo de 90 días M y ATR 14 A (BTC ≈ −18 %, una altcoin ≈ −35 %). **No** es «M − C ≥ 6·A» (seis ATR por debajo del máximo): con M=100, C=70, A=4 la primera es falsa y la segunda verdadera. Ver la nota de la versión de estabilidad al final |
| RSI | RSI diario de 14 < 30 |
| Bollinger | cierre bajo la banda inferior (20, 2) |
| Volumen de capitulación | en los 3 últimos días, vela bajista con volumen ≥ 2 × la mediana de 20 días |
| Miedo extremo | miedo y codicia ≤ 20 |
| Funding negativo | media del funding de 3 días < 0 |

Señal = caída fuerte y al menos 2 de las otras 5 con dato. Datos: Binance diario desde 2017 (BTC, ETH, XRP, SOL, ZEC del
catálogo; BNB, ADA, DOGE, LTC y LINK solo como comprobación), miedo y codicia desde 2018, funding desde 2019.
La señal se activa el 1-4 % de los días.

**Pregunta que decide**: con el mismo dinero (1 € por semana), ¿se obtiene más moneda guardándolo y comprando un tercio de
la reserva en cada día con señal, en lugar de comprar cada semana? Ventanas de 2 años cada 30 días, coste 0,1 % por compra,
lo no invertido se compra al final. **Placebo**: la misma estrategia con la señal desplazada en bloque al azar (500 veces):
mismas señales y mismos racimos, sin relación con el precio.

## Resultado
| | Ventaja frente al DCA | Placebo | p | Precio de los tramos frente al DCA | Placebo | p |
|---|---|---|---|---|---|---|
| Catálogo (5 monedas, mismo peso) | **−9,3 %** | −8,8 % | 0,52 | +0,5 % | +21,8 % | 0,03 |
| Otras 5 (robustez) | −6,3 % | −6,9 % | 0,43 | +20,9 % | +38,1 % | 0,29 |

- **Esperar a la señal con el dinero parado compra menos moneda que la compra periódica** (BTC −21 %, ETH −10 %, XRP −10 %;
  solo SOL +1 %). La causa es el efectivo parado: de media, el 44 % del dinero seguía sin invertir al final de la ventana.
  Y no es mejor que esperar a momentos al azar (p = 0,52): la señal no aporta nada a la estrategia completa.
- Lo que sí se ve: **las compras hechas en días de señal salieron a un precio parecido al del DCA, mientras que las mismas
  compras en días al azar salieron un 22 % más caras** (catálogo, p = 0,03). En las otras 5 monedas no se confirma
  (p = 0,29). Es un indicio débil de que la señal elige días relativamente baratos, no una ventaja.
- Rendimiento tras el primer día de cada episodio (descriptivo, 5-20 episodios por moneda): sin patrón estable. BTC
  a 90 días mediana −1,6 % (cualquier día +7,1 %); XRP +6,8 % (cualquier día −4,3 %). Los peores casos van del −22 % al −70 %.
- Sensibilidad (solo informa, no se elige la mejor): con 4 u 8 ATR, o pidiendo 1 o 3 condiciones más, BTC sigue negativo
  (−11 % a −24 %); el signo cambia entre monedas.

**Veredicto: ventaja no demostrada.** Por eso la página **no** dice «buen punto para acumular». Cuando se cumplen las
condiciones dice «Capitulación técnica · ventaja no demostrada», enseña qué condiciones se cumplen, un plan de tramos sobre
los soportes (presentación, no validado) y lo que pasó en este estudio. Si un estudio futuro (más datos, el registro
prospectivo) pasa el protocolo, el texto cambia a «Zona de acumulación» sin tocar código: lo decide el `veredicto` del JSON.

## Límites
- Sesgo de supervivencia: las 10 monedas siguen existiendo. Pocos episodios por moneda; ventanas solapadas (por eso el placebo).
- La regla de la estrategia (un tercio de la reserva, como mucho un tramo cada 3 días) es una de muchas; no se optimizó a
  propósito. Una regla mixta (DCA siempre + extra en la señal) queda a medio camino: no puede ganar al DCA si la reserva pierde.
- En vivo el funding de 3 días sale del registro horario propio (`datos/derivados_historial.jsonl`); con menos de 6 fotos
  cuenta como «sin dato».

## Registro prospectivo
`datos/decisiones.sqlite3`, tabla `acumulacion`: una foto por activo y día UTC (y al cambiar el estado), con las
condiciones, el precio y la versión de la señal. Con meses de registro se podrá repetir la comparación con datos que el
estudio no vio.


## Nota de la versión de estabilidad (20 nov 2026)
- **Condición exacta de la caída en `acum-1`**: `1 − C/M ≥ 6·A/C` (C = cierre diario cerrado, M = máximo de los máximos de las 90 velas diarias, A = ATR 14 de Wilder). Compara una caída porcentual con 6 veces el ATR relativo al cierre: dos denominadores distintos (M y C). La regla verbal «seis ATR por debajo del máximo» sería `M − C ≥ 6·A`. Ejemplo verificable: M=100, C=70, A=4 → `0,30 ≥ 0,343` falso con `acum-1`; `30 ≥ 24` verdadero con la verbal.
- Se conserva la regla histórica identificada como `acum-1`, porque es la que se estudió. Según el informe del 20 nov 2026, aplicar la verbal habría cambiado los días con señal de BTC de 121 a 235 y de ETH de 101 a 276: estudiarla exigiría otra versión (p. ej. `acum-2`) y repetir el estudio sin tocar los resultados de `acum-1`.
- La etiqueta sigue siendo «Capitulación técnica · ventaja no demostrada». Reproducción del estudio 11 en la auditoría: reservar efectivo para la señal obtuvo un 9,27 % menos de moneda que la compra periódica en las cinco monedas examinadas (SUI no estaba entre ellas).
- Reproducibilidad: `estudios/reproducir.py` congela las entradas, guarda el manifiesto (versiones, configuración, hashes de entradas y resultados) y comprueba que dos ejecuciones offline sobre la misma instantánea dan lo mismo.
