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

Nota: La consulta a Bubble para SC de Applicant se removió (alto consumo
de Workflow Units en Bubble). Applicant ahora resuelve su SC igual que
Student y Enrollment, directo en SAPPO.

El caller (main.py) recolecta todos los identificadores de todos los
endpoints y llama a resolve_all() una sola vez por corrida.
"""

import logging
from db_sappo import get_sc_batch_por_programa, get_sc_batch_sin_programa
from db_supabase import get_sc_desde_supabase  # agregar al import existente

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

    # --- Paso 1: SAPPO batch por (estudiante, programa) -> Student + Enrollment + Applicant juntos ---
    pares_para_sappo = list(set(pares_sappo) | set(pares_applicant))
    sc_por_programa = {}
    if pares_para_sappo:
        logger.info(f"Resolviendo SC por programa de {len(pares_para_sappo)} pares en SAPPO...")
        sc_por_programa = get_sc_batch_por_programa(pares_para_sappo)

    # --- Paso 2: Mapa aplanado por estudiante (para Billing), a partir de TODO lo ya resuelto ---
    sc_por_estudiante = {}
    for (id_est, _programa), sc in sc_por_programa.items():
        if id_est not in sc_por_estudiante and sc:
            sc_por_estudiante[id_est] = sc

    # --- Paso 3: Billing - alumnos que NO quedaron cubiertos por lo de arriba -> fallback sin programa ---
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
  

    # --- Paso final: fallback Supabase para IDs sin SC en ningún lado ---
    # Si billing (o cualquier endpoint) no pudo resolver SC vía SAPPO,
    # busca en las tablas espejo de Supabase donde ya puede estar guardado
    # de una corrida anterior (ej. ws_enrollment tiene el SC de ese alumno).
    ids_sin_sc = [
        id_est for id_est in set(ids_billing)
        if id_est not in sc_por_estudiante
    ]
    # También aplica para los pares sin SC en por_programa
    ids_sin_sc_prog = list({
        id_est for (id_est, _) in (set(pares_sappo) | set(pares_applicant))
        if not any(
            sc for (e, _), sc in sc_por_programa.items()
            if e == id_est and sc
        )
    })
    todos_sin_sc = list(set(ids_sin_sc + ids_sin_sc_prog))

    if todos_sin_sc:
        logger.info(f"{len(todos_sin_sc)} IDs sin SC → fallback Supabase")
        sc_supabase = get_sc_desde_supabase(todos_sin_sc)

        # Aplicar al mapa por_estudiante (billing)
        for id_est, sc in sc_supabase.items():
            if id_est not in sc_por_estudiante:
                sc_por_estudiante[id_est] = sc

        # Aplicar al mapa por_programa para los pares que no tienen SC
        for (id_est, programa), sc_actual in list(sc_por_programa.items()):
            if not sc_actual and id_est in sc_supabase:
                sc_por_programa[(id_est, programa)] = sc_supabase[id_est]

        # Para pares que no existen en por_programa aún
        for id_est, sc in sc_supabase.items():
            for pares in [pares_sappo, pares_applicant]:
                for (e, prog) in pares:
                    if e == id_est and (e, prog) not in sc_por_programa:
                        sc_por_programa[(e, prog)] = sc

    logger.info(
        f"SC resueltos -> por programa: {len(sc_por_programa)} pares | "
        f"por estudiante (Billing): {len(sc_por_estudiante)} IDs"
    )

    return {
        "por_programa": sc_por_programa,
        "por_estudiante": sc_por_estudiante,
    }
