"""
periodos.py
Re-exporta get_periodos() desde db_sappo para mantener la separación de responsabilidades.
La lógica de qué periodos incluir por endpoint vive aquí.
"""

from db_sappo import get_periodos   # noqa: F401  (re-export)
from datetime import date


def periodos_para_endpoint(info_periodos: dict, endpoint: str) -> list[str]:
    """
    Retorna la lista de IDs de periodo a consultar para el endpoint dado.

    Args:
        info_periodos: resultado de get_periodos()
        endpoint: 'student' | 'enrollment' | 'applicant' | 'section'
                  (billing no usa periodos, usa fechas)

    Returns:
        Lista de period IDs, ej: ['202592', '202581', '202601']
    """
    actual    = info_periodos['actual']['id']
    anterior  = info_periodos['anterior']['id'] if info_periodos['anterior'] else None
    siguiente = info_periodos['siguiente']['id'] if info_periodos['siguiente'] else None

    periodos = [actual]
    if siguiente:
        periodos.append(siguiente)

    if endpoint == 'section':
        incluir_ant = info_periodos['incluir_anterior_7']
    else:
        # enrollment, student, applicant
        incluir_ant = info_periodos['incluir_anterior_14']

    if incluir_ant and anterior:
        periodos.append(anterior)

    return periodos
