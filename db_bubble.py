"""
db_bubble.py
Consulta el endpoint de Bubble (ambiente live) para obtener el SC de Applicants.
Si Bubble no regresa SC para un IDEstudiante, el caller hace fallback a SAPPO (resolver_sc.py).

Endpoint real (Backend Workflow API de Bubble):
  GET https://comunidad.anahuaconline.com/api/1.1/wf/sociocomercial?id={IDEstudiante}
  Headers: Authorization: Bearer <BUBBLE_API_KEY>

Respuesta esperada:
  {
    "status": "success",
    "response": {
      "idBanner": "00987654",
      "sc": "AP"
    }
  }

El endpoint recibe un ID a la vez (no acepta batch), por lo que se hace
un loop con una llamada por IDEstudiante.
"""

import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BUBBLE_API_URL = os.environ.get("BUBBLE_API_URL", "")
BUBBLE_API_KEY = os.environ.get("BUBBLE_API_KEY", "")
TIMEOUT = 30


def get_sc_applicant_batch(ids_estudiante: list[str]) -> dict[str, str]:
    """
    Consulta Bubble para obtener el SC de una lista de IDEstudiante.
    Retorna {id_estudiante: SC} solo para los que Bubble conoce con status=success.
    Los que falten deberán buscarse en SAPPO (lógica en resolver_sc.py).
    """
    if not ids_estudiante or not BUBBLE_API_URL:
        logger.warning("Bubble URL no configurada o lista vacía; saltando consulta a Bubble")
        return {}

    headers = {"Authorization": f"Bearer {BUBBLE_API_KEY}"}
    resultado = {}
    fallos = 0

    for id_est in set(ids_estudiante):
        try:
            response = requests.get(
                BUBBLE_API_URL,
                params={"id": id_est},
                headers=headers,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "success":
                sc = data.get("response", {}).get("sc")
                if sc:
                    resultado[id_est] = str(sc).strip()
            else:
                # status != success -> Bubble no encontró el ID, fallback a SAPPO
                fallos += 1

        except requests.RequestException as e:
            # No truena todo el proceso si Bubble falla para un ID;
            # el fallback a SAPPO se hará en resolver_sc.py
            logger.warning(f"Bubble no respondió para IDEstudiante={id_est}: {e}")
            fallos += 1

    logger.info(
        f"SC resueltos desde Bubble: {len(resultado)}/{len(set(ids_estudiante))} IDs "
        f"({fallos} sin SC o con error -> fallback a SAPPO)"
    )
    return resultado
