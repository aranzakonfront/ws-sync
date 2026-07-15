"""
refresh_sc_billing.py
Refresca el campo SC de registros recientes en ws_billing con sc IS NULL.

Problema que resuelve:
  El job nocturno procesa billing solo para "ayer a hoy". Si en una noche
  falla la resolución de SC para algún registro (SAPPO lento, WS tronó a
  medias, etc.), ese registro queda con sc=NULL para siempre porque el
  WS de Billing no vuelve a devolver ese pago en corridas posteriores.

Qué hace:
  1. Busca en ws_billing filas con sc IS NULL y fech_pago_date >= hoy - 30 días
  2. Intenta resolver SC usando la cadena completa de fallbacks:
       SAPPO sin programa → Supabase espejo → Bubble API
  3. Actualiza solo los registros que resuelven SC (recalcula row_hash)

No vuelve a llamar al WS de Billing — solo actualiza el campo SC.

Se integra al cron nocturno en main.py, después de _procesar_billing().
También se puede correr manualmente:
    railway run python refresh_sc_billing.py
"""

import logging
import time
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("refresh_sc_billing")

from db_sappo import get_sc_batch_sin_programa
from db_bubble import get_sc_applicant_batch
from db_supabase import (
    get_client, get_sc_desde_supabase,
    calcular_hash, upsert_registros, registrar_control,
)

TABLA        = "ws_billing"
DIAS_VENTANA = 30


def get_billing_sin_sc(fecha_desde_iso: str) -> list:
    """Obtiene filas de ws_billing con sc IS NULL dentro de la ventana."""
    client = get_client()
    resultado = []
    page_size = 1000
    offset = 0

    while True:
        resp = (
            client.table(TABLA)
            .select("*")
            .is_("sc", "null")
            .gte("fech_pago_date", fecha_desde_iso)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        filas = resp.data or []
        resultado.extend(filas)
        if len(filas) < page_size:
            break
        offset += page_size

    return resultado


def resolver_sc_billing(ids_estudiante: list) -> dict:
    """
    Resuelve SC para una lista de IDEstudiante de Billing usando
    la cadena completa de fallbacks (sin programa):
      1. SAPPO sin programa
      2. Supabase espejo
      3. Bubble API
    """
    sc_resuelto = {}

    # --- Paso 1: SAPPO sin programa ---
    ids_pendientes = list(set(ids_estudiante))
    if ids_pendientes:
        sc_sappo = get_sc_batch_sin_programa(ids_pendientes)
        for id_est, sc in sc_sappo.items():
            if sc:
                sc_resuelto[id_est] = sc

    # --- Paso 2: Supabase espejo ---
    ids_pendientes = [i for i in ids_pendientes if i not in sc_resuelto]
    if ids_pendientes:
        logger.info(f"{len(ids_pendientes)} IDs → fallback Supabase espejo")
        sc_supabase = get_sc_desde_supabase(ids_pendientes)
        for id_est, sc in sc_supabase.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    # --- Paso 3: Bubble API ---
    ids_pendientes = [i for i in ids_pendientes if i not in sc_resuelto]
    if ids_pendientes:
        logger.info(f"{len(ids_pendientes)} IDs → fallback Bubble API")
        sc_bubble = get_sc_applicant_batch(ids_pendientes)
        for id_est, sc in sc_bubble.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    return sc_resuelto


def run():
    inicio = time.time()
    fecha_limite = (date.today() - timedelta(days=DIAS_VENTANA)).isoformat()

    logger.info(f"[refresh_sc_billing] Buscando ws_billing con sc=NULL y fech_pago_date >= {fecha_limite}")
    filas = get_billing_sin_sc(fecha_limite)

    if not filas:
        logger.info("[refresh_sc_billing] Sin registros con SC nulo en la ventana — nada que hacer")
        registrar_control(
            endpoint="refresh_sc_billing", periodo=None,
            registros_ws=0, sc_resueltos=0, insertados=0,
            actualizados=0, sin_cambios=0, en_queue=0,
            status="success", error_msg=None,
            duracion_seg=time.time() - inicio,
        )
        return

    logger.info(f"[refresh_sc_billing] {len(filas)} registros con SC nulo — resolviendo...")

    ids_estudiante = list({
        str(f.get("id_estudiante", "")).strip()
        for f in filas if f.get("id_estudiante")
    })

    sc_map = resolver_sc_billing(ids_estudiante)

    actualizados = 0
    sin_sc       = 0
    registros_upsert = []

    for fila in filas:
        id_est = str(fila.get("id_estudiante", "")).strip()
        sc = sc_map.get(id_est)

        if not sc:
            sin_sc += 1
            continue

        registro = {k: v for k, v in fila.items() if k not in ("row_hash", "updated_at")}
        registro["sc"] = sc
        nuevo_hash = calcular_hash(registro)

        if nuevo_hash == fila.get("row_hash"):
            sin_sc += 1
            continue

        registro["row_hash"] = nuevo_hash
        registros_upsert.append(registro)
        actualizados += 1

    if registros_upsert:
        upsert_registros(TABLA, registros_upsert, batch_size=100)

    duracion = time.time() - inicio
    logger.info(
        f"[refresh_sc_billing] Completado en {duracion:.1f}s — "
        f"revisados={len(filas)} actualizados={actualizados} sin_sc={sin_sc}"
    )
    registrar_control(
        endpoint="refresh_sc_billing", periodo=None,
        registros_ws=len(filas), sc_resueltos=actualizados,
        insertados=0, actualizados=actualizados,
        sin_cambios=sin_sc, en_queue=0,
        status="success" if sin_sc == 0 else "partial",
        error_msg=f"{sin_sc} registros sin SC tras todos los fallbacks" if sin_sc else None,
        duracion_seg=duracion,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run()
