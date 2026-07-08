"""
db_bubble.py
Consulta el endpoint de Bubble (ambiente live) para obtener el SC de alumnos.
Se usa como ÚLTIMO fallback en resolver_sc.py y fix_sc.py, solo cuando
SAPPO y las tablas espejo de Supabase no pudieron resolver el SC.

Endpoint real (Backend Workflow API de Bubble):
  GET https://comunidad.anahuaconline.com/api/1.1/wf/sociocomercial?id={IDEstudiante}
  Headers: Authorization: Bearer <BUBBLE_API_KEY>

Respuesta cuando tiene SC:
  {"status": "success", "response": {"idBanner": "00449096", "sc": "AP"}}

Respuesta cuando NO tiene SC:
  {"status": "success", "response": {"idBanner": "00449096"}}
  (el campo 'sc' simplemente no viene)

El endpoint recibe un ID a la vez (no acepta batch).
"""

import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BUBBLE_API_URL = os.environ.get("BUBBLE_API_URL", "")
BUBBLE_API_KEY = os.environ.get("BUBBLE_API_KEY", "")
TIMEOUT = 15


def get_sc_applicant_batch(ids_estudiante: list) -> dict:
    """
    Consulta Bubble para obtener el SC de una lista de IDEstudiante.
    Retorna {id_estudiante: SC} solo para los que Bubble conoce Y tienen SC.

    Notas importantes:
    - El endpoint no acepta batch: se hace una llamada por ID.
    - Si el alumno existe en Bubble pero no tiene SC asignado,
      el campo 'sc' no viene en el response — se ignora correctamente.
    - Si Bubble falla para un ID, se loguea y se continúa sin interrumpir.
    """
    if not ids_estudiante:
        return {}

    if not BUBBLE_API_URL:
        logger.warning("BUBBLE_API_URL no configurada — saltando consulta a Bubble")
        return {}

    if not BUBBLE_API_KEY:
        logger.warning("BUBBLE_API_KEY no configurada — saltando consulta a Bubble")
        return {}

    headers = {"Authorization": f"Bearer {BUBBLE_API_KEY}"}
    resultado = {}
    sin_sc = 0
    errores = 0

    for id_est in set(ids_estudiante):
        try:
            response = requests.get(
                BUBBLE_API_URL,
                params={"id": id_est},
                headers=headers,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "success":
                # El campo 'sc' puede no venir si el alumno no tiene SC en Bubble
                sc = data.get("response", {}).get("sc")
                if sc:
                    resultado[id_est] = str(sc).strip()
                else:
                    sin_sc += 1
            else:
                sin_sc += 1

        except requests.RequestException as e:
            logger.warning(f"[Bubble] Error para IDEstudiante={id_est}: {e}")
            errores += 1

    total = len(set(ids_estudiante))
    logger.info(
        f"SC desde Bubble: {len(resultado)}/{total} IDs resueltos "
        f"(sin_sc={sin_sc} errores={errores})"
    )
    return resultado
