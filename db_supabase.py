"""
db_supabase.py
Operaciones contra Supabase:
  - Cálculo de row_hash
  - Lectura de hashes existentes para detección de cambios
  - Upsert en tablas espejo
  - Inserción en sync_queue_ws
  - Registro en sync_control_ws
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"]
        )
    return _client


# ============================================================
# Utilidades
# ============================================================

def calcular_hash(record: dict, excluir: list[str] = None) -> str:
    """MD5 sobre el JSON ordenado del registro (excluyendo campos de control)."""
    excluir_set = set(excluir or []) | {"row_hash", "updated_at"}
    datos = {k: v for k, v in record.items() if k not in excluir_set}
    serializado = json.dumps(datos, sort_keys=True, default=str)
    return hashlib.md5(serializado.encode()).hexdigest()


def parsear_fecha_ws(fecha_str: str | None):
    """Convierte 'dd/mm/yyyy' (formato del WS) a objeto date, o None si inválido."""
    if not fecha_str:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(fecha_str.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


# ============================================================
# Lectura de hashes existentes
# ============================================================

def get_hashes_existentes(tabla: str, pk_cols: list[str]) -> dict[tuple, str]:
    """
    Obtiene {pk_tuple: row_hash} de todos los registros actuales en la tabla.
    Usado para detectar qué cambió sin cargar el registro completo.
    """
    client = get_client()
    resultado = {}
    page_size = 1000
    offset = 0

    cols_a_traer = ",".join(pk_cols + ["row_hash"])

    while True:
        resp = (
            client.table(tabla)
            .select(cols_a_traer)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        filas = resp.data or []
        for fila in filas:
            pk = tuple(str(fila[col]) for col in pk_cols)
            resultado[pk] = fila.get("row_hash")
        if len(filas) < page_size:
            break
        offset += page_size

    logger.debug(f"[{tabla}] {len(resultado)} hashes existentes cargados")
    return resultado


def get_filas_por_fecha(tabla: str, columna_fecha: str, fecha_desde_iso: str) -> list[dict]:
    """
    Obtiene todas las filas de `tabla` donde columna_fecha >= fecha_desde_iso.
    Usado por refresh_billing2.py para acotar el refresco a una ventana de tiempo
    (ej. solo pagos del último mes), en vez de reprocesar todo el histórico.
    """
    client = get_client()
    resultado = []
    page_size = 1000
    offset = 0

    while True:
        resp = (
            client.table(tabla)
            .select("*")
            .gte(columna_fecha, fecha_desde_iso)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        filas = resp.data or []
        resultado.extend(filas)
        if len(filas) < page_size:
            break
        offset += page_size

    logger.debug(f"[{tabla}] {len(resultado)} filas con {columna_fecha} >= {fecha_desde_iso}")
    return resultado


# ============================================================
# Upsert genérico
# ============================================================

def upsert_registros(tabla: str, registros: list[dict], batch_size: int = 500) -> int:
    """
    Hace upsert en batch a la tabla indicada.
    batch_size: reducir a 100 para tablas con payloads grandes (ej. billing)
    para evitar errores 400/520 de Supabase por request demasiado grande.
    """
    if not registros:
        return 0

    client = get_client()
    batch_size = 500
    total = 0

    for i in range(0, len(registros), batch_size):
        batch = registros[i : i + batch_size]
        client.table(tabla).upsert(batch).execute()
        total += len(batch)

    return total


# ============================================================
# sync_queue_ws
# ============================================================

def insertar_en_queue(entradas: list[dict]) -> int:
    """
    Inserta entradas en sync_queue_ws.
    Cada entrada debe tener: endpoint, tipo, id_registro, periodo (nullable), sc, payload.
    """
    if not entradas:
        return 0

    client = get_client()
    batch_size = 500
    total = 0

    for i in range(0, len(entradas), batch_size):
        batch = entradas[i : i + batch_size]
        client.table("sync_queue_ws").insert(batch).execute()
        total += len(batch)

    return total


# ============================================================
# sync_control_ws
# ============================================================

def registrar_control(
    endpoint: str,
    periodo: str | None,
    registros_ws: int,
    sc_resueltos: int,
    insertados: int,
    actualizados: int,
    sin_cambios: int,
    en_queue: int,
    status: str,
    error_msg: str | None,
    duracion_seg: float,
):
    client = get_client()
    client.table("sync_control_ws").insert({
        "endpoint": endpoint,
        "periodo": periodo,
        "registros_ws": registros_ws,
        "sc_resueltos": sc_resueltos,
        "insertados": insertados,
        "actualizados": actualizados,
        "sin_cambios": sin_cambios,
        "en_queue": en_queue,
        "status": status,
        "error_msg": error_msg,
        "duracion_seg": round(duracion_seg, 2),
    }).execute()
