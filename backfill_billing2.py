"""
backfill_billing2.py
Carga histórica de ws_billing2 desde 25/05/2026 hasta ayer.

Flujo por bloque semanal:
  1. Llama al WS /Billing con fecha_inicio y fecha_fin del bloque
  2. Resuelve SC para los IDEstudiante del bloque (SAPPO sin programa)
  3. Enriquece con SAPPO (report.totales_materias_estudiante)
  4. Hace upsert en ws_billing2 (SIN tocar sync_queue ni ws_billing)

Es idempotente: si se vuelve a correr, los registros sin cambios se saltan.

Ejecutar manualmente UNA SOLA VEZ:
    railway run python backfill_billing2.py
"""

import logging
import time
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_billing2")

from ws_client import llamar_endpoint
from db_sappo import get_sc_batch_sin_programa
from db_supabase import registrar_control
from endpoints import billing2


FECHA_INICIO = date(2026, 5, 25)
FECHA_FIN    = date.today() - timedelta(days=1)   # hasta ayer (hoy ya lo cargó el cron)
DIAS_POR_BLOQUE = 7                                 # bloques semanales


def generar_bloques(fecha_inicio: date, fecha_fin: date, dias: int) -> list:
    """Parte [fecha_inicio, fecha_fin] en bloques de `dias` días."""
    bloques = []
    actual = fecha_inicio
    while actual <= fecha_fin:
        fin_bloque = min(actual + timedelta(days=dias - 1), fecha_fin)
        bloques.append((actual, fin_bloque))
        actual = fin_bloque + timedelta(days=1)
    return bloques


if __name__ == "__main__":
    inicio_total = time.time()
    logger.info("#" * 60)
    logger.info(f"# BACKFILL BILLING2: {FECHA_INICIO} → {FECHA_FIN}")
    logger.info("#" * 60)

    bloques = generar_bloques(FECHA_INICIO, FECHA_FIN, DIAS_POR_BLOQUE)
    logger.info(f"{len(bloques)} bloques de {DIAS_POR_BLOQUE} días a procesar")

    for fi, ff in bloques:
        params = {
            "fecha_inicio": fi.strftime("%d/%m/%Y"),
            "fecha_fin":    ff.strftime("%d/%m/%Y"),
        }
        logger.info(f"--- Bloque {params['fecha_inicio']} → {params['fecha_fin']} ---")
        inicio_bloque = time.time()

        # 1. Llamar al WS /Billing
        try:
            registros = llamar_endpoint("Billing", params)
        except RuntimeError as e:
            logger.error(f"WS falló para {params}: {e}")
            registrar_control(
                endpoint="backfill_billing2", periodo=None,
                registros_ws=0, sc_resueltos=0,
                insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                status="error", error_msg=str(e),
                duracion_seg=time.time() - inicio_bloque,
            )
            continue

        if not registros:
            logger.info(f"Sin registros para {params}")
            registrar_control(
                endpoint="backfill_billing2", periodo=None,
                registros_ws=0, sc_resueltos=0,
                insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                status="success", error_msg=None,
                duracion_seg=time.time() - inicio_bloque,
            )
            continue

        # 2. Resolver SC sin programa (igual que billing normal en backfill)
        ids_estudiante = list({
            str(r.get("IDEstudiante", "")).strip()
            for r in registros if r.get("IDEstudiante")
        })
        try:
            sc_por_estudiante = get_sc_batch_sin_programa(ids_estudiante)
        except Exception as e:
            logger.error(f"Error resolviendo SC: {e}")
            sc_por_estudiante = {}

        # 3. Procesar y upsert en ws_billing2 (billing2.procesar incluye la consulta a SAPPO)
        try:
            res = billing2.procesar(registros, sc_por_estudiante, escribir_queue=False)
            registrar_control(
                endpoint="backfill_billing2", periodo=None,
                registros_ws=res.registros_ws,
                sc_resueltos=res.sc_resueltos,
                insertados=res.insertados,
                actualizados=res.actualizados,
                sin_cambios=res.sin_cambios,
                en_queue=0,
                status="success", error_msg=None,
                duracion_seg=time.time() - inicio_bloque,
            )
            logger.info(
                f"Bloque completado en {time.time()-inicio_bloque:.1f}s — "
                f"WS={res.registros_ws} new={res.insertados} "
                f"upd={res.actualizados} igual={res.sin_cambios}"
            )
        except Exception as e:
            logger.error(f"Error procesando bloque: {e}")
            registrar_control(
                endpoint="backfill_billing2", periodo=None,
                registros_ws=len(registros), sc_resueltos=0,
                insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                status="error", error_msg=str(e),
                duracion_seg=time.time() - inicio_bloque,
            )

    duracion_total = time.time() - inicio_total
    logger.info("=" * 60)
    logger.info(f"BACKFILL BILLING2 COMPLETO en {duracion_total:.1f}s")
    logger.info("=" * 60)
