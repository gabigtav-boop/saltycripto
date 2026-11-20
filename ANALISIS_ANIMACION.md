# Estudio de la animación de la portada (20 nov 2026)

Encargo: hacer más profesionales los movimientos y rebotes de la esfera de Mercado **sin cambiar su forma** (mismas partículas, colores, anillos, hilos, rótulos y tamaños). Copia previa: `.copia_previa_animacion/index.html`.

## 1. Diagnóstico de la versión anterior

| Pieza | Cómo se movía | Problema |
|---|---|---|
| Bucle | 30 fps fijos (`now − last ≥ 32 ms`) | Lo que se desplaza deprisa (paquetes de ~400 px en 1,5 s, frentes de onda) avanzaba a saltos de 8-10 px |
| Paralaje del cursor | `x += (objetivo − x)·0,04` en cada fotograma | Depende de los fps: a 60 fps llegaría casi el doble de rápido que a 30. Arranca de golpe, sin inercia |
| Resalte al señalar una franja | Encendido/apagado en un fotograma (hilos de 0,07 a 0,55, rótulo de azul a oro) | Parpadeo seco; al pasar el cursor entre franjas, saltos |
| Nivel de luz dorada (puntuación) | Igual a la puntuación en el acto | Al llegar un dato nuevo, la luz saltaba de nivel |
| Ondas | Entrada lineal en 400 ms; al terminar la vida se cortaban con un 45 % de intensidad residual | Destello al aparecer y «pop» al desaparecer; la superficie solo se levantaba, sin rebote |
| Paquetes de datos | Avance lineal sobre el parámetro de la curva de Bézier; aparición/desaparición lineal | Velocidad constante sin aceleración y desigual sobre el trazo (corre en los tramos rectos y se frena en la curva); la onda se lanzaba con un temporizador aparte (1,4 s) que podía desacompasarse del paquete |

Coste medido de un fotograma (mismo lienzo de 1890×1375, mismos datos): **2,4 ms antes y 2,4 ms después**.

## 2. Qué hace la animación profesional (fuentes)

- **Muelles en lugar de duraciones fijas.** Apple propone describir el movimiento con *respuesta* (periodo) y *amortiguamiento* en vez de duración y curva («Designing Fluid Interfaces», WWDC 2018; resúmenes de [Nathan Gitter](https://medium.com/@nathangitter/building-fluid-interfaces-ios-swift-9732bb934bf5) y [Christian Schnorr](https://medium.com/ios-os-x-development/demystifying-uikit-spring-animations-2bb868446773)). Amortiguamiento 1 = llega sin pasarse (la mayoría de los casos); por debajo de 1 aparece un rebote, que se reserva para lo que debe sentirse físico.
- **Independencia de los fotogramas.** Interpolar «un porcentaje por fotograma» o integrar con Euler cambia con los fps y se vuelve inestable con pasos grandes. Los motores de juego usan la solución exacta del muelle amortiguado ([Unreal `CriticallyDampedSmoothing`](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Core/FMath/CriticallyDampedSmoothing), [harmonica](https://github.com/charmbracelet/harmonica), [explicación](https://mathproofs.blogspot.com/2013/07/critically-damped-spring-smoothing.html?m=1)).
- **Ritmo regular antes que alto.** Un ritmo irregular se ve peor que uno más bajo y constante; en pantallas de 120 Hz conviene usar divisores exactos (60 o 30) ([Motion: cuándo los navegadores limitan rAF](https://motion.dev/magazine/when-browsers-throttle-requestanimationframe), [Kirupa](https://www.kirupa.com/animations/ensuring_consistent_animation_speeds.htm)).
- **Aceleración y frenada** (ease-in-out) para lo que viaja de un punto a otro, recorrido **por longitud de arco** para que la velocidad visible sea la de la curva de tiempo. **Entradas y salidas sin escalón** (smoothstep): nada aparece ni desaparece de golpe.
- **Movimiento reducido**: con `prefers-reduced-motion` todo llega al instante y no hay bucle.

## 3. Cambios aplicados (solo movimiento)

`web/lib/movimiento.js` (`MOV.muelle`, `suave`, `eio`, `arco`), probado en `tests/movimiento.mjs`:

| Pieza | Ahora | Parámetros |
|---|---|---|
| Paralaje del cursor | Muelle crítico exacto, con inercia | respuesta 1,1 s, z = 1 |
| Resalte de franja (hilos, rótulo, brillo de partículas) | Fundido por muelle; el rótulo mezcla azul → oro | respuesta 0,34 s, z = 1 (90 % en ≈ 0,2 s) |
| Nivel de luz dorada | Sube y baja con un rebote mínimo y se asienta | respuesta 1,5 s, z = 0,82 (rebote ≈ 1,1 % del cambio) |
| Ondas | Entrada smoothstep en 320 ms, pierden energía al avanzar y se apagan a cero exacto en el último 38 % de su vida. La superficie se levanta en la cresta y **se hunde un poco detrás** (rebote amortiguado de una onda real) | misma amplitud máxima (3 % del radio) |
| Paquetes | Ease-in-out cúbica sobre la longitud real del trazo; estela con posiciones de instantes anteriores (se estira al acelerar, se recoge al frenar); la onda sale **cuando el paquete llega** | misma duración (1,5 s entrada, 2,2 s salida) |
| Cadencia | 60 fps mientras hay algo rápido (onda de un precio, paquetes, cursor, muelles sin asentar); 30 fps con solo el giro y las ondas de fondo; siempre múltiplos del refresco | baja a 30 si un fotograma pasa de 13 ms (`lento`) |
| Pausas | Paso de tiempo limitado a 64 ms: al volver a la pestaña no hay saltos | — |

Sin cambios: forma, número y reparto de partículas, paletas de los dos temas, anillos, satélites, giro, respiración, centelleo, tamaños y textos.

## 4. Resultados

- `node tests/movimiento.mjs`: 23 comprobaciones (misma trayectoria a 30/60/120 fps con error < 1e-9; z = 1 sin rebasar; rebote medido = teoría `exp(−πz/√(1−z²))`; paso de 5 s estable; inercia al invertir el objetivo; velocidad uniforme sobre el trazo, cociente 1,0x frente a > 1,3 con el parámetro).
- En la página real (puerto 8501): resalte 0 → 0,46 → 0,91 → 0,99 a los 83, 217 y 350 ms; tras un precio, cadencia rápida durante el paquete y su onda y vuelta a 30 fps al asentarse; tema claro y oscuro; sin errores en la consola.
- Coste: 2,4 ms por fotograma, igual que antes. Por segundo, el JavaScript pasa de ~72 ms (30 fps) a ~72-144 ms según haya movimiento rápido. Solo se dibuja a 60 fps durante esos momentos.

## 5. Descartado

- **Rebote general de toda la esfera** con cada precio: con 8 activos llegando cada pocos segundos, la esfera estaría siempre temblando. El rebote queda en la onda local.
- **Rebote marcado (z < 0,6)** en la luz dorada: la luz representa una puntuación; un rebote grande mostraría durante un instante un valor que no existe. Con 0,82 se pasa un 1 %.
- **120 fps**: duplica el coste y no se percibe en movimientos de esta velocidad.
