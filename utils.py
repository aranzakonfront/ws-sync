"""
utils.py
Utilidades compartidas para limpiar datos inconsistentes que regresa el WS.
"""


def to_int_or_none(value):
    """
    Convierte un valor numérico potencialmente inconsistente del WS
    (int, float, o string con/sin decimales) a int limpio para columnas
    INTEGER de Postgres.

    El WS a veces regresa IDPersona como float (ej. 654704.0) en vez de
    int (654704), lo cual Postgres/PostgREST rechaza tal cual con:
    "invalid input syntax for type integer: '654704.0'".

    Maneja: None, "", int, float, "654704", "654704.0" -> int o None.
    """
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None
