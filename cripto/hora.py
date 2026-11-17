"""Hora de Canarias (Atlantic/Canary): UTC en invierno y UTC+1 en verano, con las reglas de la Unión Europea (el último domingo de
marzo a la 01:00 UTC empieza el horario de verano y el último domingo de octubre a la 01:00 UTC termina).

Se calcula a mano en vez de con zoneinfo: no depende de que el equipo tenga la base de zonas horarias (los servidores de GitHub
trabajan en UTC y Windows no la trae) y da lo mismo en el Mac, en la nube y en el navegador."""
import datetime as _dt
import time as _t


def _ultimo_domingo(anio, mes):
    """Instante UTC (segundos) de las 01:00 del último domingo del mes."""
    ultimo = _dt.date(anio + (mes == 12), mes % 12 + 1, 1) - _dt.timedelta(days=1)
    dom = ultimo - _dt.timedelta(days=(ultimo.weekday() + 1) % 7)
    return int(_dt.datetime(dom.year, dom.month, dom.day, 1, tzinfo=_dt.timezone.utc).timestamp())


def desfase(t=None):
    """Segundos que la hora de Canarias se adelanta a UTC en el instante t (0 o 3600)."""
    t = _t.time() if t is None else t
    anio = _dt.datetime.fromtimestamp(t, _dt.timezone.utc).year
    return 3600 if _ultimo_domingo(anio, 3) <= t < _ultimo_domingo(anio, 10) else 0


def a_canarias(t=None):
    """datetime sin zona con la hora de pared de Canarias."""
    t = _t.time() if t is None else t
    return _dt.datetime.fromtimestamp(t + desfase(t), _dt.timezone.utc).replace(tzinfo=None)


def hm(t=None):
    return a_canarias(t).strftime("%H:%M")


def hms(t=None):
    return a_canarias(t).strftime("%H:%M:%S")
