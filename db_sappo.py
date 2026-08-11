"""
db_sappo.py
Conexión a SAPPO (PostgreSQL externo, solo lectura) y queries para:
  1. Obtener periodos académicos
  2. Resolver SC (Socio Comercial) en batch
  3. Obtener totales de materias por estudiante (para billing2)
  4. Obtener tipo_materia por (estudiante, periodo, materia) (para enrollment observaciones)
"""

import os
import logging
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def get_sappo_connection():
    """Retorna una conexión activa a SAPPO. El caller es responsable de cerrarla."""
    return psycopg.connect(
        host=os.environ["SAPPO_HOST"],
        port=int(os.environ.get("SAPPO_PORT", 5432)),
        dbname=os.environ["SAPPO_DB"],
        user=os.environ["SAPPO_USER"],
        password=os.environ["SAPPO_PASSWORD"],
        connect_timeout=30,
        options="-c statement_timeout=60000"
    )


def get_periodos():
    """
    Retorna un dict con los tres periodos relevantes y las banderas
    de si se debe incluir el periodo anterior para cada endpoint.
    Usa start_date <= CURRENT_DATE para no depender de end_date
    (que a veces está incorrecta en SAPPO).
    """
    from datetime import date, timedelta

    with get_sappo_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:

            # Periodo actual: el más reciente cuyo inicio no supere hoy
            cur.execute("""
                SELECT p.id, p.arranque, p.start_date, p.end_date
                FROM core.periodo p
                WHERE p.start_date <= CURRENT_DATE
                ORDER BY p.start_date DESC
                LIMIT 1
            """)
            actual_row = cur.fetchone()
            if not actual_row:
                raise ValueError("No se encontró periodo actual en SAPPO (no hay ningún periodo con start_date <= hoy)")
            actual = dict(actual_row)

            # Periodo anterior (arranque - 1)
            cur.execute("""
                SELECT p.id, p.arranque, p.start_date, p.end_date
                FROM core.periodo p
                WHERE p.arranque = %s
                LIMIT 1
            """, (actual['arranque'] - 1,))
            row = cur.fetchone()
            anterior = dict(row) if row else None

            # Periodo siguiente (arranque + 1)
            cur.execute("""
                SELECT p.id, p.arranque, p.start_date, p.end_date
                FROM core.periodo p
                WHERE p.arranque = %s
                LIMIT 1
            """, (actual['arranque'] + 1,))
            row = cur.fetchone()
            siguiente = dict(row) if row else None

    today = date.today()
    inicio = actual['start_date']

    return {
        'actual': actual,
        'anterior': anterior,
        'siguiente': siguiente,
        'incluir_anterior_14': anterior is not None and today <= inicio + timedelta(days=14),
        'incluir_anterior_7':  anterior is not None and today <= inicio + timedelta(days=7),
    }


def get_sc_batch_por_programa(pares_estudiante_programa: list) -> dict:
    """
    Dado una lista de tuplas (id_estudiante, programa_id), retorna
    {(id_estudiante, programa_id): SC}.
    """
    if not pares_estudiante_programa:
        return {}

    pares_unicos = list(set(pares_estudiante_programa))
    ids_estudiante = [p[0] for p in pares_unicos]
    ids_programa   = [p[1] for p in pares_unicos]

    with get_sappo_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ep.estudiante_id,
                    ep.programa_id,
                    ep.socio_comercial_id AS sc
                FROM core.estudiante_programa ep
                WHERE (ep.estudiante_id, ep.programa_id) IN (
                    SELECT * FROM unnest(%s::text[], %s::text[])
                )
            """, (ids_estudiante, ids_programa))

            resultado = {}
            for row in cur.fetchall():
                id_est      = str(row[0]).strip() if row[0] else None
                programa_id = str(row[1]).strip() if row[1] else None
                sc          = str(row[2]).strip() if row[2] else None
                if id_est and programa_id:
                    resultado[(id_est, programa_id)] = sc

    logger.info(
        f"SC por programa resueltos desde SAPPO: "
        f"{len(resultado)}/{len(pares_unicos)} pares (estudiante, programa)"
    )
    return resultado


def get_sc_batch_sin_programa(ids_estudiante: list) -> dict:
    """
    Dado una lista de id_estudiante, retorna {id_estudiante: SC} tomando
    el primer registro que SAPPO devuelva, sin filtrar por programa.
    Fallback para Billing y casos sin programa confirmado.
    """
    if not ids_estudiante:
        return {}

    ids_unicos = list(set(ids_estudiante))

    with get_sappo_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (ep.estudiante_id)
                    ep.estudiante_id,
                    ep.socio_comercial_id AS sc
                FROM core.estudiante_programa ep
                WHERE ep.estudiante_id = ANY(%s)
                ORDER BY ep.estudiante_id, ep.ctid
            """, (ids_unicos,))

            resultado = {}
            for row in cur.fetchall():
                id_est = str(row[0]).strip() if row[0] else None
                sc     = str(row[1]).strip() if row[1] else None
                if id_est:
                    resultado[id_est] = sc

    logger.info(
        f"SC sin programa (fallback) resueltos desde SAPPO: "
        f"{len(resultado)}/{len(ids_unicos)} IDs"
    )
    return resultado


def get_totales_materias_batch(ids_estudiante: list) -> dict:
    """
    Consulta report.totales_materias_estudiante para una lista de IDs.
    Retorna {id_estudiante: {aprobadas, reprobadas, cursando,
                              materias_cargadas, precio_neto_materia}}.
    Usado por billing2 para enriquecer los pagos del día.
    """
    if not ids_estudiante:
        return {}

    ids_unicos = list(set(ids_estudiante))

    with get_sappo_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    tme.estudiante_id,
                    tme.aprobadas,
                    tme.reprobadas,
                    tme.cursando,
                    tme.precio_neto_materia,
                    tme.materias_cargadas
                FROM report.totales_materias_estudiante tme
                WHERE tme.estudiante_id = ANY(%s)
            """, (ids_unicos,))

            resultado = {}
            for row in cur.fetchall():
                id_est = str(row[0]).strip() if row[0] else None
                if id_est:
                    resultado[id_est] = {
                        "aprobadas":           row[1],
                        "reprobadas":          row[2],
                        "cursando":            row[3],
                        "precio_neto_materia": float(row[4]) if row[4] is not None else None,
                        "materias_cargadas":   row[5],
                    }

    logger.info(
        f"Totales materias resueltos desde SAPPO: "
        f"{len(resultado)}/{len(ids_unicos)} IDs"
    )
    return resultado


def get_tipo_materia_batch(ternas: list) -> dict:
    """
    Dado una lista de tuplas (estudiante_id, periodo_id, materia_id),
    retorna {(estudiante_id, periodo_id, materia_id): tipo_materia}.

    El valor puede ser None si no hay registro en SAPPO para esa
    combinación — se guarda como NULL en la columna observaciones.

    Query base:
        SELECT tipo_materia
        FROM report.comparativo_materias_inscritas
        WHERE periodo_id = %s
          AND estudiante_id = %s
          AND materia_id = %s
    """
    if not ternas:
        return {}

    ternas_unicas = list(set(ternas))
    ids_estudiante = [t[0] for t in ternas_unicas]
    ids_periodo    = [t[1] for t in ternas_unicas]
    ids_materia    = [t[2] for t in ternas_unicas]

    with get_sappo_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    cmi.estudiante_id,
                    cmi.periodo_id,
                    cmi.materia_id,
                    cmi.tipo_materia
                FROM report.comparativo_materias_inscritas cmi
                WHERE (cmi.estudiante_id, cmi.periodo_id, cmi.materia_id) IN (
                    SELECT * FROM unnest(%s::text[], %s::text[], %s::text[])
                )
            """, (ids_estudiante, ids_periodo, ids_materia))

            resultado = {}
            for row in cur.fetchall():
                id_est   = str(row[0]).strip() if row[0] else None
                per_id   = str(row[1]).strip() if row[1] else None
                mat_id   = str(row[2]).strip() if row[2] else None
                tipo_mat = str(row[3]).strip() if row[3] else None
                if id_est and per_id and mat_id:
                    resultado[(id_est, per_id, mat_id)] = tipo_mat

    resueltos = sum(1 for v in resultado.values() if v)
    logger.info(
        f"tipo_materia resueltos desde SAPPO: "
        f"{resueltos}/{len(ternas_unicas)} ternas "
        f"({len(ternas_unicas) - resueltos} sin valor en SAPPO → NULL)"
    )
    return resultado
