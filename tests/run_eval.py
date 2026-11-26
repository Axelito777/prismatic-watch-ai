"""Ejecuta los casos de tests/eval_cases.json contra el agente y registra trazas y resultados.

Pensado para la cuota gratuita de Gemini (20 llamadas/día por modelo):
  * --dry-run muestra cuántas llamadas costaría cada caso, sin llamar al modelo.
  * El presupuesto diario se descuenta de las llamadas ya registradas hoy en logs/traces.jsonl.
  * Es reanudable: con el mismo --lote omite los (caso, repetición) ya ejecutados.
  * Cada ejecución usa una memoria temporal y tickets/fecha aislados (no toca datos reales).

Ejemplos:
  python tests/run_eval.py --dry-run
  python tests/run_eval.py --nucleo --repeticiones 2 --lote eval1
  python tests/run_eval.py --casos faq_despacho,pedido_fuera_plazo --lote eval1
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--casos", help="ids separados por coma (por defecto, todos)")
    ap.add_argument("--nucleo", action="store_true", help="solo los casos marcados como núcleo")
    ap.add_argument("--repeticiones", type=int, default=2, help="repeticiones por caso (consistencia)")
    ap.add_argument("--lote", default=time.strftime("%Y%m%d-%H%M"), help="nombre del lote (reanudable)")
    ap.add_argument("--cuota-diaria", type=int, default=20, help="llamadas/día disponibles del modelo")
    ap.add_argument("--pausa-s", type=float, default=15, help="pausa entre ejecuciones (límite por minuto)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Aislamiento: debe quedar fijado ANTES de importar el agente.
    from src.observability.evaluacion import cargar_casos
    casos = cargar_casos()
    fecha_ref = json.loads((ROOT / "tests" / "eval_cases.json").read_text(encoding="utf-8"))["fecha_referencia"]
    logs = Path(os.getenv("OBS_LOGS_DIR", ROOT / "logs"))
    logs.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FECHA_REFERENCIA", fecha_ref)
    os.environ.setdefault("TICKETS_PATH", str(logs / "eval_tickets.csv"))

    if args.nucleo:
        casos = [c for c in casos if c.get("nucleo")]
    if args.casos:
        ids = {x.strip() for x in args.casos.split(",")}
        casos = [c for c in casos if c["id"] in ids]
    if not casos:
        sys.exit("No hay casos que ejecutar con esos filtros.")

    from src.observability.metrics import cargar_jsonl
    resultados_path = logs / "eval_results.jsonl"
    hechos = {(r["caso_id"], r["repeticion"]) for r in cargar_jsonl(resultados_path) if r.get("lote") == args.lote}
    pendientes = [(c, rep) for rep in range(1, args.repeticiones + 1) for c in casos
                  if (c["id"], rep) not in hechos]  # primero una pasada de todos los casos, luego la siguiente

    modelo = os.getenv("MODEL_NAME") or "(.env)"
    hoy = date.today().isoformat()
    usadas = sum(t.get("llm_calls", 0) for t in cargar_jsonl(logs / "traces.jsonl")
                 if str(t.get("ts", "")).startswith(hoy))
    restante = max(0, args.cuota_diaria - usadas)
    print(f"Lote '{args.lote}': {len(pendientes)} ejecuciones pendientes de {len(casos) * args.repeticiones}")
    print(f"Costo estimado: {sum(c['llm_calls_est'] for c, _ in pendientes)} llamadas al modelo. "
          f"Hoy ya se registraron {usadas}; quedan ~{restante} de {args.cuota_diaria}.")
    if args.dry_run:
        for c, rep in pendientes:
            print(f"  - {c['id']} (rep {rep}): ~{c['llm_calls_est']} llamadas")
        return

    from dotenv import load_dotenv
    load_dotenv(override=True)
    from src.agent import memory as mem
    from src.agent.graph import build_agent
    from src.observability.evaluacion import fila_resultado
    from src.observability.metrics import (consistencia_global, consistencia_por_caso, precision,
                                           resumen_trazas)
    from src.observability.tracer import escribir_jsonl, ejecutar_turno

    nombres = [v["cliente"] for v in json.loads((ROOT / "data" / "orders.json").read_text(encoding="utf-8")).values()]
    agent = build_agent()
    gastadas = 0
    for i, (caso, rep) in enumerate(pendientes):
        if gastadas + caso["llm_calls_est"] > restante:
            print(f"\nPresupuesto agotado (~{restante} llamadas). Reanuda mañana con --lote {args.lote}.")
            break
        mem.MEMORY_PATH = tempfile.mkdtemp(prefix="eval_mem_")  # memoria limpia por ejecución
        for intento in (1, 2):
            resp, traza = ejecutar_turno(
                agent, caso["pregunta"], caso["cliente"], lote=args.lote, caso_id=caso["id"],
                categoria=caso["categoria"], repeticion=rep, nombres_extra=nombres)
            tipos = {e.get("tipo") for e in traza["errores"]}
            if intento == 1 and "cuota_rpm" in tipos:
                print("  límite por minuto alcanzado: espero 65 s y reintento una vez")
                time.sleep(65)
                continue
            break
        gastadas += traza["llm_calls"]
        fila = fila_resultado(traza, caso, resp, args.lote)
        escribir_jsonl(resultados_path, fila)
        marca = "OK " if fila["acierto"] else ("INFRA" if fila["error_infra"] else "FALLA")
        fallidos = [k for k, v in fila["checks"].items() if not v]
        print(f"[{marca}] {caso['id']} rep{rep}  {traza['latencia_total_s']:.1f}s  "
              f"llm={traza['llm_calls']}  estado={traza['estado']}  {('falla: ' + ','.join(fallidos)) if fallidos else ''}")
        if "cuota_diaria" in tipos:
            print("\nCuota diaria agotada. Reanuda mañana (o con otro modelo) usando el mismo --lote.")
            break
        if i < len(pendientes) - 1:
            time.sleep(args.pausa_s)

    filas = [r for r in cargar_jsonl(resultados_path) if r.get("lote") == args.lote]
    trazas = [t for t in cargar_jsonl(logs / "traces.jsonl") if t.get("lote") == args.lote]
    por_run = {t["run_id"]: t for t in trazas}
    p = precision(filas)
    c = consistencia_global(consistencia_por_caso(filas, por_run))
    r = resumen_trazas(trazas)
    print(f"\n=== Resumen del lote '{args.lote}' ===")
    print(f"Precisión: {p['aciertos']}/{p['n']} = {p['precision']:.0%}" if p["n"] else "Precisión: sin datos")
    if c["casos"]:
        print(f"Consistencia (acuerdo de decisión): {c['acuerdo_decision']:.0%} en {c['casos']} casos con repeticiones")
    if r["latencia_s"]["n"]:
        print(f"Latencia: p50={r['latencia_s']['p50']:.1f}s  p95={r['latencia_s']['p95']:.1f}s")
    print(f"Errores: {r['tasa_error']:.0%}   Degradados: {r['tasa_degradado']:.0%}")
    print("Visualiza todo con:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
