"""
main.py
Orquestador principal del job nocturno.

Flujo:
  1. Obtener periodos desde SAPPO
  2. Lanzar 5 threads en paralelo (uno por endpoint)
     → cada thread llama al WS para sus periodos y guarda en memoria
  3. Recolectar pares (id_estudiante, CodPrograma) de Student/Enrollment/Applicant
     y los id_estudiante de Billing (sin programa)
  4. Resolver SC en batch:
       - por_programa: {(id_estudiante, programa_id): SC} -> Student, Enrollment, Applicant
       - por_estudiante: {id_estudiante: SC} -> Billing (reutiliza lo anterior + fallback SAPPO)
  5. Procesar cada endpoint: hash, upsert, sync_queue
  6. Registrar resultados en sync_control_ws
  7. Alertar si algo falló
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

from periodos import get_periodos, periodos_para_endpoint
from ws_client import llamar_endpoint
from resolver_sc import resolve_all
from db_supabase import registrar_control
from endpoints import enrollment, student, applicant, section, billing, billing2


# ============================================================
# Paso 1 y 2: Obtener datos del WS en paralelo por endpoint
# ============================================================

def fetch_endpoint_periodico(nombre_ws: str, nombre_interno: str, info_periodos: dict) -> dict:
    """
    Llama al WS para todos los periodos del endpoint.
    Retorna: {periodo_id: [registros]} o excepción capturada.
    """
    periodos = periodos_para_endpoint(info_periodos, nombre_interno)
    datos_por_periodo = {}
    errores = []

    for periodo_id in periodos:
        try:
            registros = llamar_endpoint(nombre_ws, {"periodo": periodo_id})
            datos_por_periodo[periodo_id] = registros
        except RuntimeError as e:
            logger.error(f"[{nombre_ws}][{periodo_id}] Error definitivo: {e}")
            errores.append(f"{periodo_id}: {e}")
            datos_por_periodo[periodo_id] = []   # tabla no se borra, conserva último valor

    return {"datos": datos_por_periodo, "errores": errores}


def fetch_billing(info_periodos: dict) -> dict:
    """Billing: una sola llamada por fecha (ayer/hoy), sin periodos."""
    params = billing.get_params_fecha()
    try:
        registros = llamar_endpoint("Billing", params)
        return {"datos": registros, "errores": []}
    except RuntimeError as e:
        logger.error(f"[Billing] Error definitivo: {e}")
        return {"datos": [], "errores": [str(e)]}


# ============================================================
# Ejecución principal
# ============================================================

def run():
    inicio_total = time.time()
    logger.info("=" * 60)
    logger.info("WS SYNC - Iniciando job nocturno")
    logger.info("=" * 60)

    # --- Paso 1: Periodos desde SAPPO ---
    try:
        info_periodos = get_periodos()
        actual_id = info_periodos['actual']['id']
        logger.info(
            f"Periodo actual: {actual_id} | "
            f"Anterior: {info_periodos['anterior']['id'] if info_periodos['anterior'] else 'N/A'} | "
            f"Siguiente: {info_periodos['siguiente']['id'] if info_periodos['siguiente'] else 'N/A'}"
        )
        logger.info(
            f"Incluir anterior (14 días): {info_periodos['incluir_anterior_14']} | "
            f"(7 días): {info_periodos['incluir_anterior_7']}"
        )
    except Exception as e:
        logger.critical(f"No se pudieron obtener periodos de SAPPO: {e}")
        _enviar_alerta(f"FALLA CRÍTICA: No se obtuvieron periodos de SAPPO.\n{e}")
        return

    # --- Paso 2: Fetch paralelo del WS ---
    logger.info("Lanzando 5 threads para fetch del WS en paralelo...")
    inicio_fetch = time.time()

    resultados_fetch = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futuros = {
            executor.submit(fetch_endpoint_periodico, "Student",    "student",    info_periodos): "student",
            executor.submit(fetch_endpoint_periodico, "Enrollment", "enrollment", info_periodos): "enrollment",
            executor.submit(fetch_endpoint_periodico, "Applicant",  "applicant",  info_periodos): "applicant",
            executor.submit(fetch_endpoint_periodico, "Section",    "section",    info_periodos): "section",
            executor.submit(fetch_billing,            info_periodos):                             "billing",
        }
        for futuro in as_completed(futuros):
            nombre = futuros[futuro]
            try:
                resultados_fetch[nombre] = futuro.result()
            except Exception as e:
                logger.error(f"[{nombre}] Thread falló inesperadamente: {e}")
                resultados_fetch[nombre] = {"datos": {}, "errores": [str(e)]}

    duracion_fetch = time.time() - inicio_fetch
    logger.info(f"Fetch WS completado en {duracion_fetch:.1f}s")

    # --- Paso 3: Recolectar pares (id_estudiante, CodPrograma) e IDs de Billing ---
    pares_sappo = set()      # Student + Enrollment: (id_estudiante, programa_id)
    pares_applicant = set()  # Applicant: (id_estudiante, programa_id)
    ids_billing = set()      # Billing: solo id_estudiante (no trae programa)

    for nombre in ["student", "enrollment"]:
        datos = resultados_fetch.get(nombre, {}).get("datos", {})
        for periodo_registros in datos.values():
            for r in periodo_registros:
                id_est = str(r.get("IDEstudiante", "")).strip()
                cod_programa = str(r.get("CodPrograma", "")).strip()
                if id_est and cod_programa:
                    pares_sappo.add((id_est, cod_programa))

    datos_applicant = resultados_fetch.get("applicant", {}).get("datos", {})
    for periodo_registros in datos_applicant.values():
        for r in periodo_registros:
            id_est = str(r.get("IDEstudiante", "")).strip()
            cod_programa = str(r.get("CodPrograma", "")).strip()
            if id_est and cod_programa:
                pares_applicant.add((id_est, cod_programa))

    datos_billing = resultados_fetch.get("billing", {}).get("datos", [])
    for r in (datos_billing if isinstance(datos_billing, list) else []):
        if r.get("IDEstudiante"):
            ids_billing.add(str(r["IDEstudiante"]).strip())

    logger.info(
        f"Pares (estudiante, programa) → Student/Enrollment: {len(pares_sappo)} | "
        f"Applicant: {len(pares_applicant)} | Billing (solo estudiante): {len(ids_billing)}"
    )

    # --- Paso 4: Resolver SC en batch ---
    try:
        sc_resultado = resolve_all(
            pares_sappo=list(pares_sappo),
            pares_applicant=list(pares_applicant),
            ids_billing=list(ids_billing),
        )
    except Exception as e:
        logger.error(f"Error al resolver SC: {e}. Continuando con SC vacío.")
        sc_resultado = {"por_programa": {}, "por_estudiante": {}}

    sc_por_programa = sc_resultado["por_programa"]
    sc_por_estudiante = sc_resultado["por_estudiante"]

    # --- Paso 5: Procesar cada endpoint ---
    logger.info("Procesando endpoints y escribiendo en Supabase...")

    _procesar_periodico("student",    resultados_fetch, sc_por_programa, student.procesar)
    _procesar_periodico("enrollment", resultados_fetch, sc_por_programa, enrollment.procesar)
    _procesar_periodico("applicant",  resultados_fetch, sc_por_programa, applicant.procesar)
    _procesar_periodico("section",    resultados_fetch, {}, section.procesar)
    _procesar_billing(resultados_fetch, sc_por_estudiante)
    _procesar_billing2(resultados_fetch, sc_por_estudiante)

    duracion_total = time.time() - inicio_total
    logger.info("=" * 60)
    logger.info(f"WS SYNC completado en {duracion_total:.1f}s")
    logger.info("=" * 60)


def _procesar_periodico(nombre: str, resultados_fetch: dict, sc_map: dict, fn_procesar):
    """Helper que itera periodos y llama a fn_procesar para cada uno."""
    fetch = resultados_fetch.get(nombre, {})
    datos = fetch.get("datos", {})
    errores_fetch = fetch.get("errores", [])

    if not datos and errores_fetch:
        # Todos los periodos fallaron en el WS
        registrar_control(
            endpoint=nombre, periodo=None,
            registros_ws=0, sc_resueltos=0,
            insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
            status="error",
            error_msg="; ".join(errores_fetch),
            duracion_seg=0,
        )
        return

    for periodo_id, registros in datos.items():
        inicio = time.time()
        try:
            res = fn_procesar(registros, sc_map, periodo_id)
            status = "success" if periodo_id not in str(errores_fetch) else "partial"
            registrar_control(
                endpoint=nombre, periodo=periodo_id,
                registros_ws=res.registros_ws,
                sc_resueltos=res.sc_resueltos,
                insertados=res.insertados,
                actualizados=res.actualizados,
                sin_cambios=res.sin_cambios,
                en_queue=res.en_queue,
                status=status,
                error_msg="; ".join(errores_fetch) if errores_fetch else None,
                duracion_seg=time.time() - inicio,
            )
        except Exception as e:
            logger.error(f"[{nombre}][{periodo_id}] Error procesando: {e}")
            registrar_control(
                endpoint=nombre, periodo=periodo_id,
                registros_ws=len(registros),
                sc_resueltos=0, insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
                status="error", error_msg=str(e),
                duracion_seg=time.time() - inicio,
            )


def _procesar_billing(resultados_fetch: dict, sc_por_estudiante: dict):
    """Billing: una sola corrida sin loop de periodos."""
    fetch = resultados_fetch.get("billing", {})
    registros = fetch.get("datos", [])
    errores_fetch = fetch.get("errores", [])
    inicio = time.time()

    if not registros and errores_fetch:
        registrar_control(
            endpoint="billing", periodo=None,
            registros_ws=0, sc_resueltos=0,
            insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
            status="error",
            error_msg="; ".join(errores_fetch),
            duracion_seg=0,
        )
        return

    try:
        res = billing.procesar(registros, sc_por_estudiante)
        registrar_control(
            endpoint="billing", periodo=None,
            registros_ws=res.registros_ws,
            sc_resueltos=res.sc_resueltos,
            insertados=res.insertados,
            actualizados=res.actualizados,
            sin_cambios=res.sin_cambios,
            en_queue=res.en_queue,
            status="success" if not errores_fetch else "partial",
            error_msg="; ".join(errores_fetch) if errores_fetch else None,
            duracion_seg=time.time() - inicio,
        )
    except Exception as e:
        logger.error(f"[billing] Error procesando: {e}")
        registrar_control(
            endpoint="billing", periodo=None,
            registros_ws=len(registros),
            sc_resueltos=0, insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
            status="error", error_msg=str(e),
            duracion_seg=time.time() - inicio,
        )


def _procesar_billing2(resultados_fetch: dict, sc_por_estudiante: dict):
    """Billing2: usa los mismos registros crudos del WS que Billing, enriquecidos con SAPPO."""
    fetch = resultados_fetch.get("billing", {})
    registros = fetch.get("datos", [])
    errores_fetch = fetch.get("errores", [])
    inicio = time.time()

    if not registros and errores_fetch:
        registrar_control(
            endpoint="billing2", periodo=None,
            registros_ws=0, sc_resueltos=0,
            insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
            status="error",
            error_msg="Billing WS falló; billing2 sin datos: " + "; ".join(errores_fetch),
            duracion_seg=0,
        )
        return

    try:
        res = billing2.procesar(registros, sc_por_estudiante)
        registrar_control(
            endpoint="billing2", periodo=None,
            registros_ws=res.registros_ws,
            sc_resueltos=res.sc_resueltos,
            insertados=res.insertados,
            actualizados=res.actualizados,
            sin_cambios=res.sin_cambios,
            en_queue=0,
            status="success" if not errores_fetch else "partial",
            error_msg="; ".join(errores_fetch) if errores_fetch else None,
            duracion_seg=time.time() - inicio,
        )
    except Exception as e:
        logger.error(f"[billing2] Error procesando: {e}")
        registrar_control(
            endpoint="billing2", periodo=None,
            registros_ws=len(registros), sc_resueltos=0,
            insertados=0, actualizados=0, sin_cambios=0, en_queue=0,
            status="error", error_msg=str(e),
            duracion_seg=time.time() - inicio,
        )
    """Envía alerta por email si las variables SMTP están configuradas."""
    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        logger.warning("SMTP no configurado, alerta no enviada")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(mensaje)
        msg["Subject"] = "[WS SYNC] Error en job nocturno"
        msg["From"] = os.environ.get("SMTP_USER", "")
        msg["To"] = os.environ.get("ALERT_EMAIL", "")
        with smtplib.SMTP(smtp_host, int(os.environ.get("SMTP_PORT", 587))) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.sendmail(msg["From"], [msg["To"]], msg.as_string())
        logger.info("Alerta enviada por email")
    except Exception as e:
        logger.error(f"No se pudo enviar alerta: {e}")


if __name__ == "__main__":
    run()
