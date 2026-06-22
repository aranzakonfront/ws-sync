"""
backfill.py
Carga masiva ÚNICA de datos históricos a Supabase.
NO es parte del cron nocturno (main.py) — se ejecuta manualmente UNA SOLA VEZ.

Qué carga:
  - Student, Enrollment, Applicant, Section para los periodos:
    202542, 202552, 202562, 202685, 202592, 202602, 202612, 202632
  - Billing por rango de fechas: 01/05/2025 a hoy, en bloques mensuales
    (para que cada llamada al WS sea manejable y resistente a fallos)

IMPORTANTE: NO escribe en sync_queue_ws. Bubble ya tiene su propia copia
de estos periodos/fechas históricos vía su flujo de sync independiente
(el que usaba antes de este proyecto, para periodo < 202592 directo al WS
y >= 202592 vía sus propios endpoints internos). Generar entradas de cola
aquí duplicaría el trabajo de Bubble y consumiría WU — justo lo que se
busca evitar.

Es idempotente: si se vuelve a correr, los registros que no cambiaron no
se vuelven a escribir (gracias al row_hash), así que es seguro re-ejecutar
si algo falla a medias.

Ejecutar manualmente:
    railway run python backfill.py
"""

import logging
import time
import calendar
from datetime import date
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill")

from ws_client import llamar_endpoint
from resolver_sc import resolve_all
from db_supabase import registrar_control
from endpoints import student, enrollment, applicant, section, billing


PERIODOS_BACKFILL = [
    "202542", "202552", "202562", "202585",
    "202592", "202602", "202612", "202632",
]

# Periodos que ya completaron todos sus endpoints exitosamente.
PERIODOS_SOLO_APPLICANT = ["202602", "202612"]  # solo Applicant falló en estos

# Periodos nuevos que se agregan al retry por corrección de typo (202685 → 202585)
PERIODOS_NUEVOS = ["202585"]

BILLING_FECHA_INICIO = date(2025, 5, 1)
BILLING_FECHA_FIN = date.today()


def generar_rangos_mensuales(fecha_inicio: date, fecha_fin: date) -> list:
    """Parte [fecha_inicio, fecha_fin] en bloques de un mes calendario."""
    rangos = []
    actual = date(fecha_inicio.year, fecha_inicio.month, 1)
    while actual <= fecha_fin:
        ultimo_dia = calendar.monthrange(actual.year, actual.month)[1]
        fin_mes = date(actual.year, actual.month, ultimo_dia)
        inicio_rango = max(actual, fecha_inicio)
        fin_rango = min(fin_mes, fecha_fin)
        rangos.append((inicio_rango, fin_rango))
        if actual.month == 12:
            actual = date(actual.year + 1, 1, 1)
        else:
            actual = date(actual.year, actual.month + 1, 1)
    return rangos


def fetch_periodico(nombre_ws: str, periodo: str) -> list:
    try:
        return llamar_endpoint(nombre_ws, {"periodo": periodo})
    except RuntimeError as e:
        logger.error(f"[{nombre_ws}][{periodo}] Error definitivo: {e}")
        return []


def backfill_applicant_periodos(periodos: list):
    """Recorre solo Applicant para los periodos indicados (usado en retry)."""
    logger.info(f"RETRY Applicant — Periodos: {periodos}")
    for periodo in periodos:
        logger.info(f"--- Applicant retry {periodo} ---")
        inicio_periodo = time.time()

        datos_applicant = fetch_periodico("Applicant", periodo)

        pares_applicant = set()
        for r in datos_applicant:
            id_est = str(r.get("IDEstudiante", "")).strip()
            cod_programa = str(r.get("CodPrograma", "")).strip()
            if id_est and cod_programa:
                pares_applicant.add((id_est, cod_programa))

        try:
            sc_resultado = resolve_all(
                pares_sappo=[], pares_applicant=list(pares_applicant), ids_billing=[],
            )
            sc_por_programa = sc_resultado["por_programa"]
        except Exception as e:
            logger.error(f"[{periodo}] Error resolviendo SC: {e}")
            sc_por_programa = {}

        inicio = time.time()
        try:
            res = applicant.procesar(datos_applicant, sc_por_programa, periodo, escribir_queue=False)
            registrar_control(
                endpoint="backfill_applicant", periodo=periodo,
                registros_ws=res.registros_ws, sc_resueltos=res.sc_resueltos,
                insertados=res.insertados, actualizados=res.actualizados,
                sin_cambios=res.sin_cambios, en_queue=res.en_queue,
                status="success", error_msg=None, duracion_seg=time.time() - inicio,
            )
        except Exception as e:
            logger.error(f"[backfill_applicant][{periodo}] Error: {e}")
            registrar_control(
                endpoint="backfill_applicant", periodo=periodo,
                registros_ws=len(datos_applicant), sc_resueltos=0,
                insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                status="error", error_msg=str(e), duracion_seg=time.time() - inicio,
            )
        logger.info(f"--- Applicant {periodo} completado en {time.time()-inicio_periodo:.1f}s ---")


def backfill_periodos():
    logger.info("=" * 60)
    logger.info(f"BACKFILL — Periodos: {PERIODOS_BACKFILL}")
    logger.info("=" * 60)

    for periodo in PERIODOS_BACKFILL:
        logger.info(f"--- Periodo {periodo} ---")
        inicio_periodo = time.time()

        datos_student = fetch_periodico("Student", periodo)
        datos_enrollment = fetch_periodico("Enrollment", periodo)
        datos_applicant = fetch_periodico("Applicant", periodo)
        datos_section = fetch_periodico("Section", periodo)

        # Recolectar pares (estudiante, programa) para resolver SC en batch
        pares_sappo = set()
        for r in datos_student + datos_enrollment:
            id_est = str(r.get("IDEstudiante", "")).strip()
            cod_programa = str(r.get("CodPrograma", "")).strip()
            if id_est and cod_programa:
                pares_sappo.add((id_est, cod_programa))

        pares_applicant = set()
        for r in datos_applicant:
            id_est = str(r.get("IDEstudiante", "")).strip()
            cod_programa = str(r.get("CodPrograma", "")).strip()
            if id_est and cod_programa:
                pares_applicant.add((id_est, cod_programa))

        try:
            sc_resultado = resolve_all(
                pares_sappo=list(pares_sappo),
                pares_applicant=list(pares_applicant),
                ids_billing=[],   # billing se procesa aparte, en backfill_billing()
            )
            sc_por_programa = sc_resultado["por_programa"]
        except Exception as e:
            logger.error(f"[{periodo}] Error resolviendo SC: {e}")
            sc_por_programa = {}

        # Procesar cada endpoint, SIN escribir en sync_queue_ws
        for nombre, fn_procesar, datos in [
            ("student", student.procesar, datos_student),
            ("enrollment", enrollment.procesar, datos_enrollment),
            ("applicant", applicant.procesar, datos_applicant),
            ("section", section.procesar, datos_section),
        ]:
            inicio = time.time()
            try:
                res = fn_procesar(datos, sc_por_programa, periodo, escribir_queue=False)
                registrar_control(
                    endpoint=f"backfill_{nombre}", periodo=periodo,
                    registros_ws=res.registros_ws, sc_resueltos=res.sc_resueltos,
                    insertados=res.insertados, actualizados=res.actualizados,
                    sin_cambios=res.sin_cambios, en_queue=res.en_queue,
                    status="success", error_msg=None,
                    duracion_seg=time.time() - inicio,
                )
            except Exception as e:
                logger.error(f"[backfill_{nombre}][{periodo}] Error: {e}")
                registrar_control(
                    endpoint=f"backfill_{nombre}", periodo=periodo,
                    registros_ws=len(datos), sc_resueltos=0,
                    insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                    status="error", error_msg=str(e),
                    duracion_seg=time.time() - inicio,
                )

        logger.info(f"--- Periodo {periodo} completado en {time.time()-inicio_periodo:.1f}s ---")


def backfill_billing():
    logger.info("=" * 60)
    logger.info(f"BACKFILL — Billing: {BILLING_FECHA_INICIO} a {BILLING_FECHA_FIN}")
    logger.info("=" * 60)

    rangos = generar_rangos_mensuales(BILLING_FECHA_INICIO, BILLING_FECHA_FIN)
    logger.info(f"{len(rangos)} bloques mensuales a procesar")

    for inicio_rango, fin_rango in rangos:
        params = {
            "fecha_inicio": inicio_rango.strftime("%d/%m/%Y"),
            "fecha_fin": fin_rango.strftime("%d/%m/%Y"),
        }
        logger.info(f"--- Billing {params['fecha_inicio']} a {params['fecha_fin']} ---")
        inicio = time.time()

        try:
            registros = llamar_endpoint("Billing", params)
        except RuntimeError as e:
            logger.error(f"[Billing]{params} Error definitivo: {e}")
            registrar_control(
                endpoint="backfill_billing", periodo=None,
                registros_ws=0, sc_resueltos=0, insertados=0, actualizados=0,
                sin_cambios=0, en_queue=0, status="error", error_msg=str(e),
                duracion_seg=time.time() - inicio,
            )
            continue

        ids_estudiante = list({
            str(r["IDEstudiante"]).strip()
            for r in registros if r.get("IDEstudiante")
        })

        try:
            sc_resultado = resolve_all(
                pares_sappo=[], pares_applicant=[], ids_billing=ids_estudiante,
            )
            sc_por_estudiante = sc_resultado["por_estudiante"]
        except Exception as e:
            logger.error(f"Error resolviendo SC para billing: {e}")
            sc_por_estudiante = {}

        try:
            res = billing.procesar(registros, sc_por_estudiante, escribir_queue=False)
            registrar_control(
                endpoint="backfill_billing", periodo=None,
                registros_ws=res.registros_ws, sc_resueltos=res.sc_resueltos,
                insertados=res.insertados, actualizados=res.actualizados,
                sin_cambios=res.sin_cambios, en_queue=res.en_queue,
                status="success", error_msg=None,
                duracion_seg=time.time() - inicio,
            )
        except Exception as e:
            logger.error(f"[backfill_billing] Error procesando: {e}")
            registrar_control(
                endpoint="backfill_billing", periodo=None,
                registros_ws=len(registros), sc_resueltos=0,
                insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                status="error", error_msg=str(e),
                duracion_seg=time.time() - inicio,
            )

        logger.info(f"--- Bloque completado en {time.time()-inicio:.1f}s ---")


if __name__ == "__main__":
    inicio_total = time.time()
    logger.info("#" * 60)
    logger.info("# BACKFILL RETRY — solo Billing (duplicados corregidos)")
    logger.info("#" * 60)

    backfill_billing()

    logger.info("=" * 60)
    logger.info(f"BACKFILL RETRY COMPLETO en {time.time()-inicio_total:.1f}s")
    logger.info("=" * 60)
