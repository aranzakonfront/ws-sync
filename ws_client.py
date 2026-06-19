"""
ws_client.py
Cliente HTTP para el WS origen (Banner / Academic Partnership).

Flujo de autenticación:
  1. Antes de CADA llamada de datos, se genera un token nuevo
     (el WS exige token por llamada, no reutilización entre llamadas).
  2. POST a WS_TOKEN_URL con:
       - Body (x-www-form-urlencoded): grant_type=password&username={WS_AUTH_USERNAME}
       - Header: Authorization: Basic base64(WS_AUTH_USERNAME:WS_AUTH_PASSWORD)
     Respuesta: {"access_token": "...", "token_type": "bearer", "expires_in": 299}
  3. La llamada de datos usa: Authorization: Bearer {access_token}

URLs reales:
  Token:  https://prd-apwn-int01.ban.anahuac.mx/wsAcademicPartnership/token
  Datos:  https://prd-apwn-int01.ban.anahuac.mx/wsAcademicPartnership/api/{endpoint}
          (endpoint en minúsculas: applicant, billing, enrollment, section, student)

Maneja reintentos con backoff exponencial para compensar la inestabilidad del WS.
Cada reintento también regenera el token, por si el anterior expiró durante una
llamada lenta (ej. Student tarda ~2 min).
"""

import os
import time
import logging
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

WS_BASE_URL      = os.environ.get("WS_BASE_URL", "").rstrip("/")
WS_TOKEN_URL     = os.environ.get("WS_TOKEN_URL", "")
WS_AUTH_USERNAME = os.environ.get("WS_AUTH_USERNAME", "")
WS_AUTH_PASSWORD = os.environ.get("WS_AUTH_PASSWORD", "")

WS_TIMEOUT     = int(os.environ.get("WS_TIMEOUT_SEGUNDOS", 300))
MAX_REINTENTOS = int(os.environ.get("WS_MAX_REINTENTOS", 3))
BACKOFF_SEG    = int(os.environ.get("WS_BACKOFF_SEGUNDOS", 10))
TOKEN_TIMEOUT  = 30


def _obtener_token() -> str:
    """
    Genera un token nuevo. Se llama antes de cada request de datos
    (el WS exige token por llamada).
    """
    payload = f"grant_type=password&username={WS_AUTH_USERNAME}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    resp = requests.post(
        WS_TOKEN_URL,
        data=payload,
        headers=headers,
        auth=HTTPBasicAuth(WS_AUTH_USERNAME, WS_AUTH_PASSWORD),
        timeout=TOKEN_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("access_token")
    if not token:
        raise ValueError(f"Respuesta de token sin access_token: {data}")

    return token


def llamar_endpoint(endpoint: str, params: dict) -> list[dict]:
    """
    Llama a /{endpoint} con los parámetros dados.
    Genera un token nuevo antes de cada intento.
    Reintenta hasta MAX_REINTENTOS veces con backoff exponencial.

    Args:
        endpoint: 'student' | 'enrollment' | 'applicant' | 'section' | 'billing'
                  (no es sensible a mayúsculas, se normaliza internamente)
        params:   dict con los query params (periodo, fecha_inicio, etc.)

    Returns:
        Lista de dicts con los registros del WS.

    Raises:
        RuntimeError si todos los reintentos fallan.
    """
    url = f"{WS_BASE_URL}/{endpoint.lower()}"
    ultimo_error = None

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            logger.info(f"[{endpoint}] Intento {intento}/{MAX_REINTENTOS} | params={params}")

            token = _obtener_token()
            headers = {"Authorization": f"Bearer {token}"}

            resp = requests.get(url, params=params, headers=headers, timeout=WS_TIMEOUT)
            resp.raise_for_status()

            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Respuesta inesperada del WS (no es lista): {type(data)}")

            logger.info(f"[{endpoint}] OK → {len(data)} registros")
            return data

        except (requests.RequestException, ValueError) as e:
            ultimo_error = e
            logger.warning(f"[{endpoint}] Intento {intento} falló: {e}")
            if intento < MAX_REINTENTOS:
                espera = BACKOFF_SEG * (2 ** (intento - 1))   # 10s, 20s, 40s
                logger.info(f"[{endpoint}] Esperando {espera}s antes de reintentar...")
                time.sleep(espera)

    raise RuntimeError(
        f"[{endpoint}] Falló después de {MAX_REINTENTOS} intentos. "
        f"Último error: {ultimo_error}"
    )
