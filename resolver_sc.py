"""
resolver_sc.py
Resuelve el campo SC (Socio Comercial) para todos los endpoints.

Estrategia:
  - Student, Enrollment, Applicant:
      SC depende de (id_estudiante, programa_id) -> un solo query batch
      a SAPPO con todos los pares de los tres endpoints juntos.

  - Billing:
      No trae programa_id en el response del WS. Se resuelve así:
      1. Reutiliza el SC ya calculado para ese id_estudiante en Student/
         Enrollment/Applicant de la misma corrida (sin importar de qué
         programa haya salido).
      2. Si el alumno no aparece en ninguno de esos tres, fallback a SAPPO
         sin programa (toma el primer registro que SAPPO devuelva).

  - Fallbacks adicionales (en orden):
      3. Tablas espejo de Supabase (ws_enrollment, ws_student, ws_applicant)
      4. Bubble API (último recurso, uno por uno, solo si los anteriores fallan)
"""

import logging
from db_sappo import get_sc_batch_por_programa, get_sc_batch_sin_programa
from db_bubble import get_sc_applicant_batch
from db_supabase import get_sc_desde_supabase

logger = logging.getLogger(__name__)


def resolve_all(
    pares_sappo: list,        # list[tuple[str, str]]: (id_estudiante, programa_id) de Student + Enrollment
    pares_applicant: list,    # list[tuple[str, str]]: (id_estudiante, programa_id) de Applicant
    ids_billing: list,        # list[str]: id_estudiante de Billing (sin programa)
) -> dict:
    """
    Retorna un dict con dos mapas:
      {
        'por_programa': {(id_estudiante, programa_id): SC},  # Student, Enrollment, Applicant
        'por_estudiante': {id_estudiante: SC},                # para Billing (aplanado)
      }
    """

    # --- Paso 1: SAPPO batch por (estudiante, programa) -> Student + Enrollment + Applicant ---
    pares_para_sappo = list(set(pares_sappo) | set(pares_applicant))
    sc_por_programa = {}
    if pares_para_sappo:
        logger.info(f"Resolviendo SC por programa de {len(pares_para_sappo)} pares en SAPPO...")
        sc_por_programa = get_sc_batch_por_programa(pares_para_sappo)

    # --- Paso 2: Mapa aplanado por estudiante (para Billing) ---
    sc_por_estudiante = {}
    for (id_est, _programa), sc in sc_por_programa.items():
        if id_est not in sc_por_estudiante and sc:
            sc_por_estudiante[id_est] = sc

    # --- Paso 3: Billing - fallback SAPPO sin programa ---
    ids_billing_sin_sc = [
        id_est for id_est in set(ids_billing)
        if id_est not in sc_por_estudiante
    ]
    if ids_billing_sin_sc:
        logger.info(
            f"{len(ids_billing_sin_sc)} alumnos de Billing sin SC reutilizable -> "
            f"fallback SAPPO sin programa"
        )
        sc_fallback_billing = get_sc_batch_sin_programa(ids_billing_sin_sc)
        sc_por_estudiante.update(sc_fallback_billing)

    # --- Paso 4: Fallback Supabase espejo ---
    # Recolectar todos los IDs que siguen sin SC tras SAPPO
    todos_los_ids = (
        {e for (e, _) in set(pares_sappo) | set(pares_applicant)}
        | set(ids_billing)
    )
    ids_sin_sc_supabase = [
        id_est for id_est in todos_los_ids
        if id_est not in sc_por_estudiante
    ]
    if ids_sin_sc_supabase:
        logger.info(
            f"{len(ids_sin_sc_supabase)} IDs sin SC -> fallback tablas espejo Supabase"
        )
        sc_supabase = get_sc_desde_supabase(ids_sin_sc_supabase)

        for id_est, sc in sc_supabase.items():
            # Aplicar a por_estudiante
            if id_est not in sc_por_estudiante and sc:
                sc_por_estudiante[id_est] = sc
            # Aplicar a por_programa para los pares que no tienen SC
            for pares in [pares_sappo, pares_applicant]:
                for (e, prog) in pares:
                    if e == id_est and sc_por_programa.get((e, prog)) is None and sc:
                        sc_por_programa[(e, prog)] = sc

    # --- Paso 5: Último recurso — Bubble API ---
    # Solo para IDs que siguen sin SC tras SAPPO y Supabase.
    # Bubble se consulta uno por uno (costo en WU), por eso es el último paso.
    ids_sin_sc_bubble = [
        id_est for id_est in todos_los_ids
        if id_est not in sc_por_estudiante
    ]
    if ids_sin_sc_bubble:
        logger.info(
            f"{len(ids_sin_sc_bubble)} IDs sin SC -> último fallback Bubble API"
        )
        sc_bubble = get_sc_applicant_batch(ids_sin_sc_bubble)

        for id_est, sc in sc_bubble.items():
            # Aplicar a por_estudiante
            if id_est not in sc_por_estudiante and sc:
                sc_por_estudiante[id_est] = sc
            # Aplicar a por_programa para los pares que no tienen SC
            for pares in [pares_sappo, pares_applicant]:
                for (e, prog) in pares:
                    if e == id_est and sc_por_programa.get((e, prog)) is None and sc:
                        sc_por_programa[(e, prog)] = sc

    logger.info(
        f"SC resueltos -> por programa: {len(sc_por_programa)} pares | "
        f"por estudiante (Billing): {len(sc_por_estudiante)} IDs"
    )

    return {
        "por_programa": sc_por_programa,
        "por_estudiante": sc_por_estudiante,
    }
