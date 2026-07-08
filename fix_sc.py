"""
fix_sc.py
Script de reparación de SC para registros históricos sin socio comercial.

Recorre ws_billing, ws_billing2, ws_student, ws_applicant y ws_enrollment
buscando registros donde sc IS NULL, e intenta resolverlos en este orden:
  1. SAPPO por (id_estudiante, cod_programa)   — Student, Enrollment, Applicant
  2. SAPPO sin programa (primer registro)       — fallback
  3. Tablas espejo de Supabase                  — último recurso

Actualiza directamente en Supabase con upsert (solo los que cambian de hash).
Registra el resultado en sync_control_ws con endpoint = 'fix_sc_<tabla>'.

Ejecutar manualmente:
    railway run python fix_sc.py
"""

import logging
import time
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fix_sc")

from db_sappo import (
    get_sc_batch_por_programa,
    get_sc_batch_sin_programa,
)
from db_supabase import (
    get_client,
    get_sc_desde_supabase,
    calcular_hash,
    upsert_registros,
    registrar_control,
)


# ============================================================
# Tablas y sus PKs
# ============================================================
TABLAS = [
    {
        "nombre":      "ws_billing",
        "pk":          ["id_pago", "codigo_detalle"],
        "tiene_prog":  False,   # billing no tiene cod_programa
    },
    {
        "nombre":      "ws_billing2",
        "pk":          ["id_pago", "codigo_detalle"],
        "tiene_prog":  False,
    },
    {
        "nombre":      "ws_student",
        "pk":          ["id_estudiante", "periodo"],
        "tiene_prog":  True,
        "col_prog":    "cod_programa",
    },
    {
        "nombre":      "ws_applicant",
        "pk":          ["id_estudiante", "periodo"],
        "tiene_prog":  True,
        "col_prog":    "cod_programa",
    },
    {
        "nombre":      "ws_enrollment",
        "pk":          ["id_enrollment", "periodo"],
        "tiene_prog":  True,
        "col_prog":    "cod_programa",
    },
]


def get_filas_sin_sc(tabla: str) -> list[dict]:
    """Obtiene todas las filas donde sc IS NULL, paginando de 1000 en 1000."""
    client = get_client()
    resultado = []
    page_size = 1000
    offset = 0

    while True:
        resp = (
            client.table(tabla)
            .select("*")
            .is_("sc", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        filas = resp.data or []
        resultado.extend(filas)
        if len(filas) < page_size:
            break
        offset += page_size

    return resultado


def resolver_sc_para_filas(filas: list[dict], tiene_prog: bool,
                            col_prog: str = "cod_programa") -> dict:
    """
    Dado una lista de filas sin SC, intenta resolverlo en tres pasos:
      1. SAPPO por (id_estudiante, programa)  — si la tabla tiene programa
      2. SAPPO sin programa                   — fallback
      3. Supabase espejo                       — último recurso
    Retorna {id_estudiante: SC}.
    """
    ids_estudiante = list({
        str(f.get("id_estudiante", "")).strip()
        for f in filas if f.get("id_estudiante")
    })

    sc_resuelto = {}

    # --- Paso 1: SAPPO con programa ---
    if tiene_prog:
        pares = list({
            (str(f.get("id_estudiante", "")).strip(),
             str(f.get(col_prog, "")).strip())
            for f in filas
            if f.get("id_estudiante") and f.get(col_prog)
        })
        if pares:
            sc_prog = get_sc_batch_por_programa(pares)
            # Aplanar a {id_estudiante: SC} tomando el primero disponible
            for (id_est, _), sc in sc_prog.items():
                if sc and id_est not in sc_resuelto:
                    sc_resuelto[id_est] = sc

    # --- Paso 2: SAPPO sin programa (fallback) ---
    ids_sin_sc = [i for i in ids_estudiante if i not in sc_resuelto]
    if ids_sin_sc:
        sc_sin_prog = get_sc_batch_sin_programa(ids_sin_sc)
        for id_est, sc in sc_sin_prog.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    # --- Paso 3: Supabase espejo ---
    ids_sin_sc = [i for i in ids_estudiante if i not in sc_resuelto]
    if ids_sin_sc:
        sc_supabase = get_sc_desde_supabase(ids_sin_sc)
        for id_est, sc in sc_supabase.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    return sc_resuelto


def fix_tabla(config: dict):
    """Repara SC en una tabla específica."""
    tabla      = config["nombre"]
    tiene_prog = config["tiene_prog"]
    col_prog   = config.get("col_prog", "cod_programa")
    inicio     = time.time()

    logger.info(f"[{tabla}] Buscando filas sin SC...")
    filas = get_filas_sin_sc(tabla)

    if not filas:
        logger.info(f"[{tabla}] Sin filas con SC nulo — nada que reparar")
        registrar_control(
            endpoint=f"fix_sc_{tabla}", periodo=None,
            registros_ws=0, sc_resueltos=0, insertados=0,
            actualizados=0, sin_cambios=0, en_queue=0,
            status="success", error_msg=None,
            duracion_seg=time.time() - inicio,
        )
        return

    logger.info(f"[{tabla}] {len(filas)} filas sin SC — resolviendo...")
    sc_map = resolver_sc_para_filas(filas, tiene_prog, col_prog)

    actualizados = 0
    sin_sc       = 0
    registros_upsert = []

    for fila in filas:
        id_est = str(fila.get("id_estudiante", "")).strip()
        sc = sc_map.get(id_est)

        if not sc:
            sin_sc += 1
            continue

        # Reconstruir registro con SC y nuevo hash
        registro = {k: v for k, v in fila.items() if k not in ("row_hash", "updated_at")}
        registro["sc"] = sc
        nuevo_hash = calcular_hash(registro)

        if nuevo_hash == fila.get("row_hash"):
            # El hash no cambió (SC ya era el mismo, caso raro)
            continue

        registro["row_hash"] = nuevo_hash
        registros_upsert.append(registro)
        actualizados += 1

    if registros_upsert:
        upsert_registros(tabla, registros_upsert, batch_size=100)

    duracion = time.time() - inicio
    logger.info(
        f"[{tabla}] Completado en {duracion:.1f}s — "
        f"revisados={len(filas)} actualizados={actualizados} sin_sc={sin_sc}"
    )
    registrar_control(
        endpoint=f"fix_sc_{tabla}", periodo=None,
        registros_ws=len(filas),
        sc_resueltos=actualizados,
        insertados=0,
        actualizados=actualizados,
        sin_cambios=sin_sc,
        en_queue=0,
        status="success" if sin_sc == 0 else "partial",
        error_msg=f"{sin_sc} IDs sin SC tras todos los fallbacks" if sin_sc else None,
        duracion_seg=duracion,
    )


if __name__ == "__main__":
    inicio_total = time.time()
    logger.info("#" * 60)
    logger.info("# FIX SC — Reparación de registros históricos sin SC")
    logger.info("#" * 60)

    for config in TABLAS:
        try:
            fix_tabla(config)
        except Exception as e:
            logger.error(f"[{config['nombre']}] Error inesperado: {e}")

    logger.info("=" * 60)
    logger.info(f"FIX SC COMPLETO en {time.time()-inicio_total:.1f}s")
    logger.info("=" * 60)
