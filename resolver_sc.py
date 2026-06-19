"""
resolver_sc.py
Resuelve el campo SC (Socio Comercial) para todos los endpoints.

Estrategia:
  - Student, Enrollment:
      SC depende de (id_estudiante, programa_id) -> SAPPO batch con pares.

  - Applicant:
      1. Bubble primero (solo necesita id_estudiante, regresa el registro
         más reciente del alumno sin importar programa).
      2. Fallback a SAPPO por (id_estudiante, programa_id) para los que
         Bubble no resolvió.

  - Billing:
      No trae programa_id en el response del WS. Se resuelve así:
      1. Reutiliza el SC ya calculado para ese id_estudiante en Student/
         Enrollment/Applicant de la misma corrida (sin importar de qué
         programa haya salido).
      2. Si el alumno no aparece en ninguno de esos tres, fallback a SAPPO
         sin programa (toma el primer registro que SAPPO devuelva).

El caller (main.py) recolecta todos los identificadores de todos los
endpoints y llama a resolve_all() una sola vez por corrida.
"""

import logging
from db_sappo import get_sc_batch_por_programa, get_sc_batch_sin_programa
from db_bubble import get_sc_applicant_batch

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

    # --- Paso 1: Applicant vía Bubble (solo necesita id_estudiante) ---
    ids_applicant = list({p[0] for p in pares_applicant})
    sc_bubble = {}
    if ids_applicant:
        logger.info(f"Resolviendo SC de {len(ids_applicant)} Applicants en Bubble...")
        sc_bubble = get_sc_applicant_batch(ids_applicant)

    # Pares de Applicant cuyo alumno NO fue resuelto por Bubble -> fallback SAPPO con programa
    pares_applicant_sin_sc = [
        p for p in set(pares_applicant) if p[0] not in sc_bubble
    ]
    if pares_applicant_sin_sc:
        logger.info(
            f"{len(pares_applicant_sin_sc)} pares de Applicant sin SC en Bubble -> fallback SAPPO"
        )

    # --- Paso 2: SAPPO batch por (estudiante, programa) -> Student + Enrollment + fallback Applicant ---
    pares_para_sappo = list(set(pares_sappo) | set(pares_applicant_sin_sc))
    sc_por_programa = {}
    if pares_para_sappo:
        logger.info(f"Resolviendo SC por programa de {len(pares_para_sappo)} pares en SAPPO...")
        sc_por_programa = get_sc_batch_por_programa(pares_para_sappo)

    # --- Paso 3: Combinar resultado de Applicant (Bubble) en sc_por_programa ---
    # Para que applicant.py pueda consultar por (id_estudiante, programa_id) de forma uniforme,
    # agregamos también los resueltos por Bubble bajo cada par (id_estudiante, programa_id)
    # que sí pidió Applicant para ese alumno.
    pares_applicant_por_id = {}
    for id_est, programa_id in pares_applicant:
        pares_applicant_por_id.setdefault(id_est, []).append(programa_id)

    for id_est, sc in sc_bubble.items():
        for programa_id in pares_applicant_por_id.get(id_est, []):
            sc_por_programa[(id_est, programa_id)] = sc

    # --- Paso 4: Mapa aplanado por estudiante (para Billing), a partir de TODO lo ya resuelto ---
    sc_por_estudiante = {}
    for (id_est, _programa), sc in sc_por_programa.items():
        if id_est not in sc_por_estudiante and sc:
            sc_por_estudiante[id_est] = sc

    # --- Paso 5: Billing - alumnos que NO quedaron cubiertos por lo de arriba -> fallback sin programa ---
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

    logger.info(
        f"SC resueltos -> por programa: {len(sc_por_programa)} pares | "
        f"por estudiante (Billing): {len(sc_por_estudiante)} IDs"
    )

    return {
        "por_programa": sc_por_programa,
        "por_estudiante": sc_por_estudiante,
    }
