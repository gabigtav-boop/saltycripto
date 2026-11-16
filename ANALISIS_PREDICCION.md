# Análisis del sistema de predicción y casos (16 nov 2026)

Pregunta: ¿es fiable la flecha de analogías, tienen relación los casos de distintas temporalidades y compensa rehacerlo?

## Veredicto
- **No predice la dirección.** Con 8 monedas, 3,9 años y unas 19.000 pruebas fuera de muestra independientes, acierta el signo el 51-52 % de las veces. Es indistinguible de lanzar una moneda.
- **Los casos de cada temporalidad casi no se relacionan.** Son búsquedas separadas, cada una con rasgos de su propio plazo.
- **Lo único que aporta valor medible es el rango (el abanico).** Y ese valor viene de la volatilidad, no de los casos.
- **Se rehízo** alrededor de lo que se puede medir (ver «Lo que se hizo» y «Diseño»). Parchear el sistema anterior no podía arreglar que la señal no existe.

## Cómo funciona hoy
Cada temporalidad (1 h, 4 h, 1 d, 1 sem) describe cada vela con 14 rasgos de ese mismo plazo, busca los k momentos pasados más parecidos y usa lo que pasó después. Se valida con el último 35 % del histórico y el cono se ensancha para cubrir lo que promete. Si la validación no muestra destreza, las analogías se ignoran y la flecha queda «sin dirección clara».

## Qué medí y cómo
- Puntos de prueba **separados por el horizonte**, para que sus resultados no se solapen. La validación interna usa puntos cada cuarto de horizonte: con unos 130 puntos solo hay unos 30 independientes, y un acierto del 42 % o del 72 % entra en el ruido.
- Intervalos de confianza por **bootstrap de meses completos**, porque todas las criptomonedas se mueven a la vez.
- Métricas: correlación de rangos (IC) entre lo previsto y lo real, acierto de signo y destreza (pérdida pinball frente a la distribución de siempre).

## Resultados

| Prueba | Datos | Resultado |
|---|---|---|
| Sistema actual (réplica fiel con lectura v21) | 22 series, 2.257 pruebas | IC +0,03 · acierto 52,5 % · destreza negativa en 21 de 22 |
| Sistema, 8 monedas, desde 2023 | 1 d: 1.616 · 4 h: 5.656 · 1 h: 11.320 pruebas | IC +0,03 / +0,03 / +0,01 · acierto 52,0 / 51,0 / 51,0 % |
| Regresión de cresta con rasgos del plazo | mismas | IC -0,02 / +0,01 / +0,01 · acierto 48-50 % |
| La misma con contexto de plazos mayores | mismas | IC -0,02 / +0,02 / +0,01 · sin mejora |
| Los 4 casos que se muestran | 2.263 pruebas | acierto 51,5 % · dispersión 0,95 frente a 1,30 real |
| Lectura técnica v21 sola, 2018-2026 | 6 monedas, diario | IC por año: solo 2021 y 2026 positivos; global +0,03 |

Los 4 casos mostrados aparentan más certeza de la que hay: su dispersión es menor que la de los resultados reales.

## Relación entre temporalidades
Mismo instante, misma moneda, 63 cierres diarios por 7 monedas.

| Par | Fechas de casos que coinciden (±1 día) | Al azar | Correlación de medianas | Mismo signo |
|---|---|---|---|---|
| 1 h y 4 h | 38 % | 24 % | +0,35 | 60 % |
| 4 h y 1 d | 20 % | 8 % | +0,18 | 53 % |
| 1 h y 1 d | 10 % | 8 % | +0,18 | 54 % |

Los resultados reales sí están muy correlacionados (+0,63 / +0,49 / +0,31) porque los horizontes están anidados. Las predicciones comparten mucho menos: no cuentan una historia coherente. Cuando las tres coinciden en el signo, el acierto es del 55 % (n=147), sin significación. Ejemplo real: ETH con probabilidad de subida 0,75 en 1 h y 0,36 en 1 d a la vez.

## Qué sí se puede medir y funciona
1. **Volatilidad.** Un estimador de memoria larga mejora el rango entre un 0,7 y un 1,6 % de pérdida pinball frente al actual, en 8 de 8 monedas en 1 h y 4 h. Es poco, pero es consistente.
2. **Probabilidad de tocar un nivel** (las zonas de entrada y salida) usando la volatilidad del momento: mejora de la puntuación de Brier de entre un 8 y un 15 % frente a no mirar la volatilidad, en 8 de 8 monedas y en las tres temporalidades. Está razonablemente calibrada; con el sistema actual de sigma sobreestima las probabilidades altas.
3. El rango actual cubre bien (50 % y 80 % reales: 47-51 % y 79-81 %).

## Limitaciones de la primera parte (cubiertas después)
- El periodo de prueba era 2023-2026, sin mercado bajista profundo, y solo se probaron analogías y cresta con datos de precio y volumen. La ampliación de abajo repite el protocolo con 2022 dentro, árboles de decisión y datos de flujo de órdenes, funding, base de futuros y miedo y codicia.
- Siguen pendientes: ocho monedas muy correlacionadas (por eso los intervalos son por meses completos), redes neuronales y datos sin historia profunda (interés abierto, reparto largo/corto, libro de órdenes, noticias).

## Ampliación: los límites del estudio que se pudieron cubrir
Se repitió el protocolo con **flujo de órdenes** (volumen comprador agresivo de cada vela), **funding**, **base de futuros** (prima sobre el índice), **miedo y codicia** y el **movimiento de BTC**, con **regresión de cresta y árboles de decisión** (scikit-learn, en un entorno aparte), una prueba que **empieza en 2020, 2021 y 2022** (el mercado bajista de 2022 queda dentro) y evaluación **económica con costes** (10 puntos básicos por lado).

| Plazo (pruebas) | Mejor IC | IC 90 % | Acierto | R² fuera de muestra |
|---|---|---|---|---|
| 1 d (2.681) | +0,035 (árboles) | [-0,007, +0,075] | 51-52 % | negativo en los 4 modelos |
| 4 h (8.530) | +0,027 (árboles) | [+0,001, +0,049] | 50,5 % | -1,2 % |
| 1 h (14.240) | +0,013 (cresta con datos nuevos) | [-0,009, +0,036] | 49,7 % | -0,2 % |

- **Los datos nuevos no aportan de forma consistente:** el IC con y sin ellos es casi el mismo y su signo cambia según el plazo y el modelo.
- **Mercado bajista de 2022:** el IC por año ronda cero en 4 h y 1 h; en 1 d la regresión simple llega a -0,16 ese año. Ningún modelo funciona de forma sostenida.
- **Economía:** la rentabilidad activa frente a comprar y mantener (neta de costes) tiene un t máximo de 1,16 entre 12 combinaciones: ninguna es significativa, y con la corrección por probar 12 combinaciones el p es 1,00. Los modelos de árboles aparentan buenos resultados porque están largos el 51-61 % del tiempo en un mercado que subió, no por acertar.
- **Lo que no se pudo cubrir:** el interés abierto, el reparto de cuentas en largo y los datos de liquidaciones solo tienen unos 30 días de historia en las APIs públicas. Para poder estudiarlos más adelante, el Mac guarda desde ahora una foto por hora en `datos/derivados_historial.jsonl`.
- **Lo que queda sin probar:** redes neuronales, datos de libro de órdenes con historia y noticias con historia.

## Lo que se hizo (16 nov 2026)
`cripto/proyeccion.py` se rehízo con el diseño de abajo y la interfaz del Estudio cambió: sin flecha ni casos, con el rango, la probabilidad de llegar a cada nivel (en la tarjeta y en las etiquetas del gráfico) y la comprobación fuera de muestra en pantalla. Calibrar todo el histórico tarda menos de 1 s (antes, unos 40 s). El histórico profundo que se descarga pasó a 8.000 velas de 1 h, 5.000 de 4 h y 2.500 diarias.

## Casos fiables (estudio 9)
Pregunta: ¿se pueden hacer casos «como los de antes pero fiables y reales»? Se probaron conjuntos de rasgos de similitud (8 monedas, 1 h / 4 h / 1 d, prueba desde 2023, K = 50 casos, puntos separados por el horizonte).

| Qué se midió | Resultado |
|---|---|
| Destreza de la distribución de los casos frente a la de siempre, con cualquier conjunto de rasgos (volatilidad, tendencia, plazos mayores) | de -0,3 % a -0,9 %: **los casos no aportan información** sobre la forma de lo que viene |
| Dispersión de los 4 casos más parecidos frente a la real | 0,70-0,73: **aparentan una coherencia que no existe** |
| Dispersión de 50 casos frente a la real | 0,95-0,99: reproducen la real |
| Cobertura de la envolvente de los 50 casos (50 % / 80 %) | 47-50 % / 76-78 % |
| 5 casos elegidos a los percentiles 10-25-50-75-90 (cobertura 50 % / 80 %) | 49-51 % / 76-78 % |

Conclusión: los casos no pueden ser una señal, pero sí pueden ser una **muestra fiel de episodios reales**. Por eso se buscan 50 (no los 4 más parecidos), se dibujan solo 5, elegidos para representar de verdad la distribución (una nube de más caminos tapaba el gráfico y la dispersión ya la enseña el abanico), la similitud usa volatilidad y tendencia (lo que hace que se vean situaciones comparables sin empeorar nada) y su dispersión se comprueba fuera de muestra en la propia pantalla.

## Diseño (implementado)
1. **Un único modelo de volatilidad** para todas las temporalidades: mezcla de la volatilidad reciente (media móvil exponencial de 20 velas) y la de largo plazo (100). Mejoró al estimador anterior en 8 de 8 monedas en 1 h, 4 h y 1 d; no se usó la regresión HAR porque en 1 d no mejoró y necesita ajuste. Así los rangos de 1 h, 4 h y 1 d son coherentes por construcción.
2. **Rango sin deriva** a partir de la distribución histórica del retorno normalizado por la volatilidad, y **probabilidad de llegar a cada nivel** (dos entradas y dos salidas) a partir de la distribución histórica de la mayor excursión del precio en el plazo.
3. **Sin flecha. Casos reales representativos** (ver más abajo). La tarjeta de Proyección dice que no se predice la dirección y por qué.
4. **Comprobación en pantalla:** en la tarjeta (cobertura real y mejora de las probabilidades en el plazo abierto) y en Datos (la tabla de todas las monedas y plazos). Con menos de 30 comprobaciones se avisa de que la muestra es pequeña.
5. Solo reintroducir dirección si un dato nuevo demuestra destreza con este mismo protocolo (`estudios/`). El registro de derivados acumula historia para poder probar el interés abierto y el reparto largo/corto.

Pendiente a propósito: la «Lectura de los datos» (siete factores) sigue usando sus pesos fijos sin validar; ahora se llama «acuerdo» y no «confianza».

Reproducción: `estudios/LEEME.md`.
