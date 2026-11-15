"""Panel cripto en directo (solo análisis, sin operar). Arranca con: streamlit run app.py"""
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import indicators as ind

st.set_page_config(page_title="Cripto Intel", layout="wide")


@st.cache_resource
def get_feed():
    if config.DEMO:
        from demo import DemoFeed
        f = DemoFeed(list(config.COINS))
    else:
        from kraken import LiveFeed
        f = LiveFeed(config.COINS)
    f.start()
    return f


@st.cache_data(ttl=60, show_spinner=False)
def get_candles(name: str, tf: str) -> pd.DataFrame:
    minutes = config.TIMEFRAMES[tf]
    if config.DEMO:
        import demo
        return demo.candles(name, minutes)
    import kraken
    return kraken.fetch_ohlc(config.COINS[name][1], minutes)


def fmt(x: float) -> str:
    d = 2 if x >= 100 else 3 if x >= 1 else 5
    return f"{x:,.{d}f} €"


feed = get_feed()
st.title("Cripto Intel · en directo")
st.caption("Solo análisis. No ejecuta órdenes y no es consejo financiero.")

if "sel" not in st.session_state:
    st.session_state.sel = "BTC"


@st.fragment(run_every=2)
def panel():
    snap = feed.snapshot()
    cols = st.columns(4)
    for i, name in enumerate(config.COINS):
        d = snap.get(name)
        with cols[i % 4]:
            with st.container(border=True):
                if d and time.time() - d["ts"] < 60:
                    st.metric(name, fmt(d["last"]), f"{d['chg']:+.2f} % 24h")
                else:
                    st.metric(name, "sin datos")
    st.caption(f"Estado: {feed.status} · actualizado {time.strftime('%H:%M:%S')}")


panel()

c1, c2 = st.columns([1, 1])
st.session_state.sel = c1.selectbox("Moneda", list(config.COINS), index=list(config.COINS).index(st.session_state.sel))
tf = c2.radio("Temporalidad", list(config.TIMEFRAMES), index=1, horizontal=True)
name = st.session_state.sel


@st.fragment(run_every=30)
def chart():
    try:
        df = get_candles(name, tf)
    except Exception as e:  # noqa: BLE001
        st.error(f"No se pudieron cargar las velas: {e}")
        return
    df = df.copy()
    df["ema20"], df["ema50"] = ind.ema(df["close"], 20), ind.ema(df["close"], 50)
    rsi, atr = ind.rsi(df["close"]).iloc[-1], ind.atr(df).iloc[-1]
    last = feed.snapshot().get(name, {}).get("last", df["close"].iloc[-1])
    a, b, c = st.columns(3)
    a.metric("Precio", fmt(last))
    b.metric("RSI 14", f"{rsi:.0f}")
    c.metric("ATR 14", f"{atr / last * 100:.1f} % del precio")
    v = df.tail(120)
    fig = go.Figure([go.Candlestick(x=v["time"], open=v["open"], high=v["high"], low=v["low"], close=v["close"], name=name),
                     go.Scatter(x=v["time"], y=v["ema20"], name="EMA20", line=dict(width=1.2)),
                     go.Scatter(x=v["time"], y=v["ema50"], name="EMA50", line=dict(width=1.2))])
    fig.add_hline(y=last, line_dash="dot", opacity=.5)
    fig.update_layout(height=480, margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False,
                      template="plotly_dark", legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)


chart()
