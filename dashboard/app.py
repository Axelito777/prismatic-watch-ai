"""Dashboard de observabilidad del agente Prismatic Watch AI.

Ejecutar:  streamlit run dashboard/app.py
Lee logs/traces.jsonl, logs/eval_results.jsonl y logs/eval_offline.jsonl (carpeta configurable con
la variable de entorno OBS_LOGS_DIR). Todos los datos personales ya vienen enmascarados.
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.observability import metrics as M  # noqa: E402

LOGS = Path(os.getenv("OBS_LOGS_DIR", ROOT / "logs"))
PLANTILLA = "plotly_white"
COLORES = {"ok": "#2a9d8f", "degradado": "#e9c46a", "error": "#e76f51", "bloqueado": "#6c757d"}
CUOTA_DIARIA = 20

st.set_page_config(page_title="Observabilidad · Prismatic Watch AI", page_icon="⌚", layout="wide")


def umbral_actual() -> float:
    try:
        txt = (ROOT / "src" / "agent" / "tools.py").read_text(encoding="utf-8")
        return float(re.search(r"UMBRAL_DISTANCIA\s*=\s*([\d.]+)", txt).group(1))
    except Exception:
        return 20.0


@st.cache_data(show_spinner=False)
def cargar(nombre: str, firma: float) -> list:
    return M.cargar_jsonl(LOGS / nombre)


def firma(nombre: str) -> float:
    p = LOGS / nombre
    return p.stat().st_mtime if p.exists() else 0.0


trazas_raw = cargar("traces.jsonl", firma("traces.jsonl"))
eval_raw = cargar("eval_results.jsonl", firma("eval_results.jsonl"))
offline_raw = cargar("eval_offline.jsonl", firma("eval_offline.jsonl"))

st.title("⌚ Observabilidad · Prismatic Watch AI")
st.caption("Métricas de precisión, consistencia, errores, latencia y uso de recursos sobre las ejecuciones registradas.")

if not trazas_raw and not offline_raw:
    st.info(
        "Todavía no hay datos en `logs/`. Genera algunos:\n\n"
        "```\npython tests/eval_offline.py          # sin gastar cuota del modelo\n"
        "python tests/run_eval.py --dry-run     # estima el costo de la evaluación completa\n"
        "python tests/run_eval.py --nucleo --lote eval1\npython src/main.py                     # uso interactivo (también se registra)\n```")
    st.stop()

# ---------------- datos en DataFrames ----------------
df = pd.DataFrame(trazas_raw)
if not df.empty:
    df["ts"] = pd.to_datetime(df["ts"])
    df["lote"] = df["lote"].fillna("(interactivo)")
    df["categoria"] = df["categoria"].fillna("(interactivo)")
    df["modelo"] = df["modelo"].fillna("(sin dato)")
    df["herramientas_usadas"] = df["herramientas"].apply(lambda hs: ", ".join(sorted({h["nombre"] for h in hs})) or "—")
    df["n_errores"] = df["errores"].apply(len)

with st.sidebar:
    st.header("Filtros")
    if st.button("Recargar datos"):
        st.cache_data.clear()
        st.rerun()
    if not df.empty:
        lotes = st.multiselect("Lote", sorted(df["lote"].unique()), default=sorted(df["lote"].unique()))
        cats = st.multiselect("Categoría", sorted(df["categoria"].unique()), default=sorted(df["categoria"].unique()))
        modelos = st.multiselect("Modelo", sorted(df["modelo"].unique()), default=sorted(df["modelo"].unique()))
        dff = df[df["lote"].isin(lotes) & df["categoria"].isin(cats) & df["modelo"].isin(modelos)]
    else:
        dff = df
    st.caption(f"Registros: {len(dff)} de {len(df)} turnos")

trazas = [t for t in trazas_raw if t["run_id"] in set(dff["run_id"])] if not dff.empty else []
por_run = {t["run_id"]: t for t in trazas}
runs = set(por_run)
evals = [e for e in eval_raw if e["run_id"] in runs]
resumen = M.resumen_trazas(trazas) if trazas else None
prec = M.precision(evals) if evals else None
cons_caso = M.consistencia_por_caso(evals, por_run) if evals else {}
cons = M.consistencia_global(cons_caso)


def pct(x):
    return "—" if x is None else f"{x:.0%}"


def seg(x):
    return "—" if x is None else f"{x:.1f} s"


tabs = st.tabs(["Resumen", "Latencia", "Precisión y consistencia", "Errores y seguridad",
                "Recursos", "Herramientas y búsqueda", "Trazas"])

# ---------------- Resumen ----------------
with tabs[0]:
    if resumen:
        c = st.columns(4)
        c[0].metric("Turnos analizados", resumen["turnos"])
        c[1].metric("Precisión (casos de evaluación)", pct(prec["precision"]) if prec else "—",
                    help="Aciertos / ejecuciones válidas. Excluye fallas de infraestructura (cuota, modelo no disponible).")
        c[2].metric("Consistencia de decisión", pct(cons["acuerdo_decision"]),
                    help="Promedio, por caso, de la fracción de repeticiones que tomó la misma decisión (herramientas + ticket).")
        c[3].metric("Tasa de error", pct(resumen["tasa_error"]))
        c = st.columns(4)
        c[0].metric("Latencia p50", seg(resumen["latencia_s"]["p50"]))
        c[1].metric("Latencia p95", seg(resumen["latencia_s"]["p95"]))
        c[2].metric("Llamadas al modelo / turno",
                    "—" if resumen["llm_calls"]["media"] is None else f"{resumen['llm_calls']['media']:.1f}")
        c[3].metric("Tokens / turno",
                    "—" if resumen["tokens_total"]["media"] is None else f"{resumen['tokens_total']['media']:.0f}")
        if prec:
            st.caption(f"Precisión: {prec['aciertos']}/{prec['n']} · IC95% Wilson "
                       f"[{pct(prec['ic95'][0])}, {pct(prec['ic95'][1])}] · "
                       f"{prec['excluidas_por_infra']} ejecuciones excluidas por fallas de infraestructura")
        else:
            st.caption("Sin resultados de evaluación en el filtro actual: ejecuta `python tests/run_eval.py`.")
        col1, col2 = st.columns(2)
        est = dff["estado"].value_counts().rename_axis("estado").reset_index(name="turnos")
        col1.plotly_chart(px.pie(est, names="estado", values="turnos", hole=0.5, color="estado",
                                 color_discrete_map=COLORES, template=PLANTILLA, title="Estado de los turnos"),
                          )
        diario = dff.groupby(dff["ts"].dt.strftime("%Y-%m-%d")).size().rename("turnos").reset_index()
        col2.plotly_chart(px.bar(diario, x="ts", y="turnos", template=PLANTILLA, title="Turnos por día",
                                 labels={"ts": "día"}).update_xaxes(type="category"))
    else:
        st.info("No hay trazas del agente en el filtro actual (la evaluación offline está en su pestaña).")

# ---------------- Latencia ----------------
with tabs[1]:
    ok = dff[dff["estado"].isin(["ok", "degradado"])] if not dff.empty else dff
    if ok.empty:
        st.info("Sin turnos completados para medir latencia.")
    else:
        c1, c2 = st.columns(2)
        c1.plotly_chart(px.histogram(ok, x="latencia_total_s", nbins=20, marginal="box", template=PLANTILLA,
                                     title="Distribución de la latencia por turno (s)",
                                     labels={"latencia_total_s": "segundos"}))
        nod = M.latencia_por_nodo(trazas)
        if nod:
            dn = pd.DataFrame([{"nodo": k, "media": v["media"], "p95": v["p95"], "n": v["n"]} for k, v in nod.items()])
            fig = go.Figure()
            fig.add_bar(x=dn["nodo"], y=dn["media"], name="media")
            fig.add_bar(x=dn["nodo"], y=dn["p95"], name="p95")
            fig.update_layout(template=PLANTILLA, barmode="group", title="Latencia por nodo del grafo (s)")
            c2.plotly_chart(fig)
        frac = M.fraccion_llm_en_latencia(trazas)
        c1, c2 = st.columns(2)
        if frac is not None:
            c1.metric("Tiempo de espera del modelo sobre el total", pct(frac),
                      help="Suma de la duración de las llamadas al LLM / latencia total de los turnos.")
        c2.plotly_chart(px.box(ok, x="categoria", y="latencia_total_s", color="categoria", template=PLANTILLA,
                               title="Latencia por categoría de caso (s)"))
        st.plotly_chart(px.scatter(ok, x="llm_calls", y="latencia_total_s", color="categoria", hover_data=["caso_id"],
                                   template=PLANTILLA, title="Más llamadas al modelo → más latencia",
                                   labels={"llm_calls": "llamadas al modelo en el turno", "latencia_total_s": "segundos"}),
                        )

# ---------------- Precisión y consistencia ----------------
with tabs[2]:
    if not evals:
        st.info("Sin resultados de evaluación. Ejecuta `python tests/run_eval.py --nucleo --lote eval1`.")
    else:
        pc = M.precision_por_categoria(evals)
        dp = pd.DataFrame([{"categoria": k, "precision": v["precision"], "n": v["n"], "lo": v["ic95"][0], "hi": v["ic95"][1]}
                           for k, v in pc.items() if v["n"]])
        c1, c2 = st.columns(2)
        if not dp.empty:
            fig = go.Figure(go.Bar(x=dp["categoria"], y=dp["precision"], text=dp["n"].apply(lambda n: f"n={n}"),
                                   error_y=dict(type="data", symmetric=False, array=dp["hi"] - dp["precision"],
                                                arrayminus=dp["precision"] - dp["lo"])))
            fig.update_layout(template=PLANTILLA, yaxis=dict(range=[0, 1.05], tickformat=".0%"),
                              title="Precisión por categoría (IC95% de Wilson)")
            c1.plotly_chart(fig)
        fallos = pd.DataFrame([{"caso": e["caso_id"], "check": k} for e in evals for k, v in e["checks"].items() if not v])
        if not fallos.empty:
            ff = fallos.groupby(["caso", "check"]).size().rename("veces").reset_index()
            c2.plotly_chart(px.bar(ff, x="caso", y="veces", color="check", template=PLANTILLA,
                                   title="Qué verificaciones fallan, por caso"))
        else:
            c2.success("Todas las verificaciones pasaron en las ejecuciones filtradas.")

        st.subheader("Consistencia entre repeticiones")
        if cons_caso:
            dc = pd.DataFrame([{"caso": k, **v} for k, v in cons_caso.items()])
            fig = go.Figure()
            fig.add_bar(x=dc["caso"], y=dc["acuerdo_decision"], name="acuerdo de decisión")
            fig.add_bar(x=dc["caso"], y=dc["similitud_texto"], name="similitud del texto")
            fig.update_layout(template=PLANTILLA, barmode="group", yaxis=dict(range=[0, 1.05], tickformat=".0%"),
                              title="Consistencia por caso (≥ 2 repeticiones)")
            st.plotly_chart(fig)
            st.dataframe(dc.rename(columns={"decisiones_distintas": "decisiones distintas"}),
                         hide_index=True)
        else:
            st.info("La consistencia necesita al menos 2 repeticiones del mismo caso (`--repeticiones 2`).")

        st.subheader("Resultado por ejecución")
        de = pd.DataFrame(evals)[["lote", "caso_id", "categoria", "repeticion", "acierto", "estado", "latencia_s", "huella"]]
        st.dataframe(de, hide_index=True)

# ---------------- Errores y seguridad ----------------
with tabs[3]:
    if not trazas:
        st.info("Sin trazas en el filtro actual.")
    else:
        errs = M.errores_por_tipo(trazas)
        c1, c2 = st.columns(2)
        if errs:
            de = pd.DataFrame({"tipo": list(errs), "ocurrencias": list(errs.values())})
            c1.plotly_chart(px.bar(de, x="tipo", y="ocurrencias", template=PLANTILLA, title="Errores por tipo"),
                            )
        else:
            c1.success("Sin errores registrados en el filtro actual.")
        alertas = pd.Series([a for t in trazas for a in t.get("alertas", [])]).value_counts()
        if not alertas.empty:
            c2.plotly_chart(px.bar(alertas.rename_axis("alerta").reset_index(name="veces"), x="alerta", y="veces",
                                   template=PLANTILLA, title="Ajustes del evaluador (replanificación)"),
                            )
        else:
            c2.info("El evaluador no emitió alertas.")
        codigos = pd.Series([h["codigo"] for t in trazas for h in t.get("herramientas", [])]).value_counts()
        c1, c2 = st.columns(2)
        if not codigos.empty:
            c1.plotly_chart(px.bar(codigos.rename_axis("código").reset_index(name="veces"), x="código", y="veces",
                                   template=PLANTILLA, title="Códigos devueltos por las herramientas"),
                            )
        st.subheader("Seguridad y privacidad")
        s = st.columns(4)
        s[0].metric("Entradas bloqueadas (posible prompt injection)", int((dff["estado"] == "bloqueado").sum()))
        s[1].metric("Mensajes con datos personales enmascarados", int(dff["datos_enmascarados"].sum()))
        s[2].metric("Turnos degradados (error silenciado)", int((dff["estado"] == "degradado").sum()),
                    help="El agente siguió funcionando con un plan genérico porque falló una llamada interna al modelo.")
        s[3].metric("Búsquedas sin resultados", int(sum(t.get("reintentos_busqueda", 0) for t in trazas)))
        malos = dff[(dff["estado"].isin(["error", "degradado"]))][["ts", "caso_id", "estado", "n_errores", "pregunta"]]
        if not malos.empty:
            st.dataframe(malos, hide_index=True)

# ---------------- Recursos ----------------
with tabs[4]:
    comp = dff[dff["estado"] != "bloqueado"] if not dff.empty else dff
    if comp.empty:
        st.info("Sin turnos con consumo de recursos.")
    else:
        hoy = date.today().isoformat()
        usadas = int(df[df["ts"].dt.date.astype(str) == hoy]["llm_calls"].sum()) if not df.empty else 0
        c = st.columns(4)
        c[0].metric(f"Llamadas al modelo hoy (cuota ≈ {CUOTA_DIARIA}/día)", usadas,
                    delta=f"{CUOTA_DIARIA - usadas} restantes", delta_color="inverse" if usadas >= CUOTA_DIARIA else "normal")
        c[1].metric("Tokens totales", f"{int(comp['tokens_total'].sum()):,}")
        c[2].metric("CPU promedio / turno", f"{comp['cpu_s'].mean():.2f} s")
        c[3].metric("Memoria pico (RSS)", f"{comp['rss_mb'].max():.0f} MB")
        d = comp.reset_index(drop=True).reset_index().rename(columns={"index": "turno"})
        fig = go.Figure()
        fig.add_bar(x=d["turno"], y=d["tokens_in"], name="tokens de entrada")
        fig.add_bar(x=d["turno"], y=d["tokens_out"], name="tokens de salida")
        fig.update_layout(template=PLANTILLA, barmode="stack", title="Tokens por turno", xaxis_title="turno (orden cronológico)")
        st.plotly_chart(fig)
        c1, c2 = st.columns(2)
        c1.plotly_chart(px.histogram(comp, x="llm_calls", template=PLANTILLA, title="Llamadas al modelo por turno"),
                        )
        llm_nodo = pd.DataFrame([{"nodo": c_["nodo"], "llamadas": 1, "tokens": c_["tokens_total"]}
                                 for t in trazas for c_ in t.get("llm", [])])
        if not llm_nodo.empty:
            g = llm_nodo.groupby("nodo").sum().reset_index()
            c2.plotly_chart(px.bar(g, x="nodo", y="llamadas", text="tokens", template=PLANTILLA,
                                   title="¿Qué nodo consume más llamadas? (texto = tokens)"))

# ---------------- Herramientas y búsqueda (offline) ----------------
with tabs[5]:
    if not offline_raw:
        st.info("Ejecuta `python tests/eval_offline.py` (no usa cuota del modelo).")
    else:
        off = pd.DataFrame(offline_raw)
        ult = off[off["lote"] == off["lote"].max()]
        her = ult[ult["tipo"] == "herramienta"]
        ret = ult[ult["tipo"] == "retrieval"]
        c = st.columns(4)
        c[0].metric("Herramientas correctas", f"{int(her['acierto'].sum())}/{len(her)}")
        ind = ret[ret["categoria"] == "en_dominio"]
        fuera = ret[ret["categoria"].str.startswith("fuera_dominio")]
        c[1].metric("Búsqueda: hit@3 en dominio", f"{int(ind['acierto'].sum())}/{len(ind)}")
        c[2].metric("Rechazo de consultas fuera de dominio", f"{int(fuera['acierto'].sum())}/{len(fuera)}")
        if not ret.empty:
            c[3].metric("Latencia de búsqueda p50", f"{ret['latencia_s'].median() * 1000:.0f} ms")
            u = umbral_actual()
            fig = px.scatter(ret, x="dist_min", y="caso_id", color="categoria", symbol="acierto", template=PLANTILLA,
                             title=f"Distancia mínima por consulta vs. umbral actual ({u:g})",
                             labels={"dist_min": "distancia al mejor fragmento", "caso_id": ""})
            fig.add_vline(x=u, line_dash="dash", annotation_text="umbral")
            st.plotly_chart(fig)
        fall = ult[~ult["acierto"]][["tipo", "caso_id", "esperado", "obtenido"]]
        st.subheader("Casos que fallaron (candidatos a hallazgo)")
        if fall.empty:
            st.success("Todos los casos offline pasaron.")
        else:
            st.dataframe(fall, hide_index=True)
        with st.expander("Todos los resultados offline"):
            st.dataframe(ult, hide_index=True)

# ---------------- Trazas ----------------
with tabs[6]:
    if dff.empty:
        st.info("Sin trazas.")
    else:
        cols = ["ts", "lote", "caso_id", "categoria", "estado", "latencia_total_s", "llm_calls", "tokens_total",
                "herramientas_usadas", "ticket", "prioridad", "run_id"]
        st.dataframe(dff.sort_values("ts", ascending=False)[cols], hide_index=True)
        elegido = st.selectbox("Inspeccionar una traza", dff.sort_values("ts", ascending=False)["run_id"].tolist(),
                               format_func=lambda r: f"{r} · {por_run[r].get('caso_id') or 'interactivo'} · {por_run[r]['estado']}")
        t = por_run[elegido]
        st.markdown(f"**Pregunta (enmascarada):** {t['pregunta']}")
        st.markdown(f"**Respuesta (enmascarada):** {t['respuesta'] or '—'}")
        filas = ([("nodo · " + n["nodo"], n["inicio_s"], n["duracion_s"], "nodo") for n in t["nodos"]]
                 + [("llm · " + (c_["nodo"] or "?"), c_["inicio_s"], c_["duracion_s"], "modelo") for c_ in t["llm"]]
                 + [("tool · " + h["nombre"], h["inicio_s"], h["duracion_s"], "herramienta") for h in t["herramientas"]])
        if filas:
            g = pd.DataFrame(filas, columns=["paso", "inicio", "duracion", "tipo"])
            fig = px.bar(g, base="inicio", x="duracion", y="paso", color="tipo", orientation="h", template=PLANTILLA,
                         title="Línea de tiempo del turno (s)")
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig)
        if t["herramientas"]:
            st.dataframe(pd.DataFrame(t["herramientas"])[["nombre", "codigo", "duracion_s", "args"]],
                         hide_index=True)
        if t["errores"]:
            st.warning(t["errores"])
