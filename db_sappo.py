"""
db_sappo.py
Conexión a SAPPO (PostgreSQL externo, solo lectura) y queries para:
  1. Obtener periodos académicos
  2. Resolver SC (Socio Comercial) en batch:
     - get_sc_batch_por_programa(): por (estudiante_id, programa_id) -> usado en
       Student, Enrollment y fallback de Applicant.
     - get_sc_batch_sin_programa(): por estudiante_id solo, toma el primer
       registro -> usado como último fallback (Billing sin match, Applicant
       sin programa_id confirmado).

Usa psycopg (v3) en vez de psycopg2: el paquete psycopg[binary] empaqueta
su propia copia de libpq, evitando el error "libpq.so.5: cannot open
shared object file" que ocurre con psycopg2-binary en algunos entornos
de Railway/Nixpacks.
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
        options="-c statement_timeout=60000"   # 60 seg máx por query
    )


def get_periodos():
    """
    Retorna un dict con los tres periodos relevantes y las banderas
    de si se debe incluir el periodo anterior para cada endpoint.

    Estructura retornada:
    {
        'actual':   {'id': '202592', 'arranque': 2, 'start_date': date(...), 'end_date': date(...)},
        'anterior': {'id': '202581', 'arranque': 1, ...} | None,
        'siguiente':{'id': '202601', 'arranque': 3, ...} | None,
        'incluir_anterior_14': bool,   # Enrollment, Student, Applicant
        'incluir_anterior_7':  bool,   # Section
    }
    """
    from datetime import date, timedelta

    with get_sappo_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:

            # Periodo actual
            cur.execute("""
                SELECT p.id, p.arranque, p.start_date, p.end_date
                FROM core.periodo p
                WHERE CURRENT_DATE BETWEEN p.start_date AND p.end_date
                LIMIT 1
            """)
            actual_row = cur.fetchone()
            if not actual_row:
                raise ValueError("No se encontró periodo actual en SAPPO (CURRENT_DATE no está en ningún periodo)")
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
    Dado una lista de tuplas (id_estudiante, programa_id), retorna un dict
    {(id_estudiante, programa_id): SC}.

    Usado para Student, Enrollment y Applicant (vía fallback), donde el SC
    depende del programa específico del alumno.

    Query real:
        SELECT ep.socio_comercial_id
        FROM core.estudiante_programa ep
        WHERE ep.estudiante_id = %s AND ep.programa_id = %s
    """
    if not pares_estudiante_programa:
        return {}

    pares_unicos = list(set(pares_estudiante_programa))
    ids_estudiante = [p[0] for p in pares_unicos]
    ids_programa = [p[1] for p in pares_unicos]

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
                id_est = str(row[0]).strip() if row[0] else None
                programa_id = str(row[1]).strip() if row[1] else None
                sc = str(row[2]).strip() if row[2] else None
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
    el primer registro que SAPPO devuelva para ese alumno, sin filtrar por programa.

    Usado únicamente como fallback para Billing, cuando el alumno no aparece
    en Student/Enrollment/Applicant de la misma corrida (no hay SC ya
    resuelto que reutilizar).
    """
    if not ids_estudiante:
        return {}

    ids_unicos = list(set(ids_estudiante))

    with get_sappo_connection() as conn:
        with conn.cursor() as cur:
            # DISTINCT ON + ctid: toma el primer registro físico que SAPPO
            # devuelva por estudiante_id, sin imponer una regla de "más reciente".
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
                sc = str(row[1]).strip() if row[1] else None
                if id_est:
                    resultado[id_est] = sc

    logger.info(
        f"SC sin programa (fallback) resueltos desde SAPPO: "
        f"{len(resultado)}/{len(ids_unicos)} IDs"
    )
    return resultado
