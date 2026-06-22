"""
load_from_json.py
Carga datos de Student (u otro endpoint) desde un archivo JSON local a Supabase.
Útil cuando el WS regresó datos pero no se pudieron capturar en el backfill
(ej. Student 202602 que sí tiene data pero el WS devolvió 0 en el cron).

Uso:
    railway run python load_from_json.py

Configura las variables ARCHIVO_JSON, ENDPOINT y PERIODO abajo antes de correr.
"""

import json
import logging
import time
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("load_from_json")

# ============================================================
# CONFIGURAR ANTES DE CORRER
# ============================================================
ARCHIVO_JSON = "student_202602.json"   # nombre del archivo JSON en la raíz del proyecto
ENDPOINT = "student"                    # 'student' | 'enrollment' | 'applicant' | 'section'
PERIODO = "202602"                      # periodo al que pertenecen estos datos
# ============================================================

from resolver_sc import resolve_all
from db_supabase import registrar_control
from endpoints import student, enrollment, applicant, section


def procesar_endpoint(registros: list, endpoint: str, periodo: str):
    """Procesa los registros por el mismo pipeline que el backfill, sin tocar sync_queue."""

    # Resolver SC
    pares_sappo = set()
    pares_applicant_set = set()

    if endpoint in ("student", "enrollment"):
        for r in registros:
            id_est = str(r.get("IDEstudiante", "")).strip()
            cod_programa = str(r.get("CodPrograma", "")).strip()
            if id_est and cod_programa:
                pares_sappo.add((id_est, cod_programa))

    elif endpoint == "applicant":
        for r in registros:
            id_est = str(r.get("IDEstudiante", "")).strip()
            cod_programa = str(r.get("CodPrograma", "")).strip()
            if id_est and cod_programa:
                pares_applicant_set.add((id_est, cod_programa))

    try:
        sc_resultado = resolve_all(
            pares_sappo=list(pares_sappo),
            pares_applicant=list(pares_applicant_set),
            ids_billing=[],
        )
        sc_por_programa = sc_resultado["por_programa"]
    except Exception as e:
        logger.error(f"Error resolviendo SC: {e}")
        sc_por_programa = {}

    # Mapear al procesador correcto
    fn_map = {
        "student":    student.procesar,
        "enrollment": enrollment.procesar,
        "applicant":  applicant.procesar,
        "section":    section.procesar,
    }
    fn_procesar = fn_map.get(endpoint)
    if not fn_procesar:
        raise ValueError(f"Endpoint desconocido: {endpoint}")

    inicio = time.time()
    try:
        res = fn_procesar(registros, sc_por_programa, periodo, escribir_queue=False)
        registrar_control(
            endpoint=f"json_load_{endpoint}", periodo=periodo,
            registros_ws=res.registros_ws, sc_resueltos=res.sc_resueltos,
            insertados=res.insertados, actualizados=res.actualizados,
            sin_cambios=res.sin_cambios, en_queue=res.en_queue,
            status="success", error_msg=None,
            duracion_seg=time.time() - inicio,
        )
        logger.info(
            f"[{endpoint}][{periodo}] Completado: "
            f"WS={res.registros_ws} SC={res.sc_resueltos} "
            f"new={res.insertados} upd={res.actualizados} "
            f"igual={res.sin_cambios}"
        )
    except Exception as e:
        logger.error(f"Error procesando {endpoint}/{periodo}: {e}")
        registrar_control(
            endpoint=f"json_load_{endpoint}", periodo=periodo,
            registros_ws=len(registros), sc_resueltos=0,
            insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
            status="error", error_msg=str(e),
            duracion_seg=time.time() - inicio,
        )


if __name__ == "__main__":
    logger.info(f"Cargando {ARCHIVO_JSON} → endpoint={ENDPOINT} periodo={PERIODO}")

    with open(ARCHIVO_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalizar: acepta lista directa o wrapper {"response": [...]}
    if isinstance(data, list):
        registros = data
    elif isinstance(data, dict):
        registros = data.get("response") or data.get("data") or []
    else:
        raise ValueError("Formato JSON no reconocido")

    logger.info(f"{len(registros)} registros cargados del archivo")
    procesar_endpoint(registros, ENDPOINT, PERIODO)
