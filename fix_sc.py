"""
fix_sc.py
Script de reparación de SC para registros históricos sin socio comercial.

Recorre ws_billing, ws_billing2, ws_student, ws_applicant y ws_enrollment
buscando registros donde sc IS NULL, e intenta resolverlos en este orden:
  1. SAPPO por (id_estudiante, cod_programa)
  2. SAPPO sin programa (primer registro)
  3. Tablas espejo de Supabase
  4. Bubble API (ultimo recurso — uno por uno, solo si no lo tiene sc en el response)

Ejecutar manualmente:
    railway run python fix_sc.py
"""

import logging
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fix_sc")

from db_sappo import get_sc_batch_por_programa, get_sc_batch_sin_programa
from db_supabase import (
    get_client, get_sc_desde_supabase,
    calcular_hash, upsert_registros, registrar_control,
)

# ============================================================
# Configuración Bubble
# ============================================================
BUBBLE_SC_URL   = "https://comunidad.anahuaconline.com/api/1.1/wf/sociocomercial"
BUBBLE_SC_TOKEN = os.environ.get("BUBBLE_API_KEY", "")   # Bearer token en variable de entorno


# ============================================================
# Tablas y sus PKs
# ============================================================
TABLAS = [
    {"nombre": "ws_billing",    "pk": ["id_pago", "codigo_detalle"], "tiene_prog": False},
    {"nombre": "ws_billing2",   "pk": ["id_pago", "codigo_detalle"], "tiene_prog": False},
    {"nombre": "ws_student",    "pk": ["id_estudiante", "periodo"],  "tiene_prog": True,  "col_prog": "cod_programa"},
    {"nombre": "ws_applicant",  "pk": ["id_estudiante", "periodo"],  "tiene_prog": True,  "col_prog": "cod_programa"},
    {"nombre": "ws_enrollment", "pk": ["id_enrollment", "periodo"],  "tiene_prog": True,  "col_prog": "cod_programa"},
]


def get_filas_sin_sc(tabla: str) -> list:
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


def get_sc_desde_bubble(ids_estudiante: list) -> dict:
    """
    Consulta Bubble uno por uno para los IDs que siguen sin SC.
    Solo guarda el SC si viene en el response (campo 'sc' puede no venir si no lo tiene).
    """
    if not ids_estudiante or not BUBBLE_SC_TOKEN:
        if not BUBBLE_SC_TOKEN:
            logger.warning("BUBBLE_API_KEY no configurada — saltando fallback Bubble")
        return {}

    headers = {"Authorization": f"Bearer {BUBBLE_SC_TOKEN}"}
    resultado = {}
    resueltos = 0
    sin_sc_bubble = 0

    for id_est in ids_estudiante:
        try:
            resp = requests.get(
                BUBBLE_SC_URL,
                params={"id": id_est},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "success":
                # El campo 'sc' puede no venir si el alumno no tiene SC en Bubble
                sc = data.get("response", {}).get("sc")
                if sc:
                    resultado[id_est] = str(sc).strip()
                    resueltos += 1
                else:
                    sin_sc_bubble += 1
            else:
                sin_sc_bubble += 1

        except Exception as e:
            logger.warning(f"[Bubble] Error para {id_est}: {e}")
            sin_sc_bubble += 1

    logger.info(
        f"[Bubble] resueltos={resueltos} sin_sc={sin_sc_bubble} "
        f"de {len(ids_estudiante)} IDs consultados"
    )
    return resultado


def resolver_sc_para_filas(filas: list, tiene_prog: bool,
                            col_prog: str = "cod_programa") -> dict:
    """
    Intenta resolver SC en 4 pasos:
      1. SAPPO con programa
      2. SAPPO sin programa
      3. Supabase espejo
      4. Bubble API
    """
    ids_estudiante = list({
        str(f.get("id_estudiante", "")).strip()
        for f in filas if f.get("id_estudiante")
    })

    sc_resuelto = {}

    # --- Paso 1: SAPPO con programa ---
    if tiene_prog:
        pares = list({
            (str(f.get("id_estudiante", "")).strip(), str(f.get(col_prog, "")).strip())
            for f in filas if f.get("id_estudiante") and f.get(col_prog)
        })
        if pares:
            sc_prog = get_sc_batch_por_programa(pares)
            for (id_est, _), sc in sc_prog.items():
                if sc and id_est not in sc_resuelto:
                    sc_resuelto[id_est] = sc

    # --- Paso 2: SAPPO sin programa ---
    ids_sin_sc = [i for i in ids_estudiante if i not in sc_resuelto]
    if ids_sin_sc:
        sc_sin_prog = get_sc_batch_sin_programa(ids_sin_sc)
        for id_est, sc in sc_sin_prog.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    # --- Paso 3: Supabase espejo ---
    ids_sin_sc = [i for i in ids_estudiante if i not in sc_resuelto]
    if ids_sin_sc:
        logger.info(f"{len(ids_sin_sc)} IDs sin SC → buscando en tablas espejo Supabase")
        sc_supabase = get_sc_desde_supabase(ids_sin_sc)
        for id_est, sc in sc_supabase.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    # --- Paso 4: Bubble API ---
    ids_sin_sc = [i for i in ids_estudiante if i not in sc_resuelto]
    if ids_sin_sc:
        logger.info(f"{len(ids_sin_sc)} IDs sin SC → consultando Bubble")
        sc_bubble = get_sc_desde_bubble(ids_sin_sc)
        for id_est, sc in sc_bubble.items():
            if sc and id_est not in sc_resuelto:
                sc_resuelto[id_est] = sc

    return sc_resuelto


def fix_tabla(config: dict):
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
    sin_sc = 0
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
        registros_ws=len(filas), sc_resueltos=actualizados,
        insertados=0, actualizados=actualizados, sin_cambios=sin_sc, en_queue=0,
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
