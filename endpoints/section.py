"""
endpoints/section.py
Procesa registros del WS /Section. No requiere SC.
"""

import logging
from dataclasses import dataclass
from db_supabase import (
    calcular_hash, get_hashes_existentes, upsert_registros
)

logger = logging.getLogger(__name__)
TABLA = "ws_section"
PK_COLS = ["id_grupo", "periodo"]


@dataclass
class ResultadoEndpoint:
    registros_ws: int = 0
    sc_resueltos: int = 0
    insertados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    en_queue: int = 0  # Section no va a sync_queue (Bubble no la necesita por ahora)


def procesar(registros_ws: list[dict], sc_dict: dict, periodo: str) -> ResultadoEndpoint:
    if not registros_ws:
        logger.info(f"[Section][{periodo}] Sin registros del WS")
        return ResultadoEndpoint()

    res = ResultadoEndpoint(registros_ws=len(registros_ws))
    hashes_existentes = get_hashes_existentes(TABLA, PK_COLS)

    registros_upsert = []

    for r in registros_ws:
        id_grupo = str(r.get("IDGrupo", "")).strip()
        if not id_grupo:
            logger.warning(f"[Section][{periodo}] Registro sin IDGrupo, se omite")
            continue

        registro = {
            "id_grupo":          id_grupo,
            "periodo":           periodo,
            "universidad":       r.get("Universidad"),
            "campus":            r.get("Campus"),
            "sub_periodo":       r.get("SubPeriodo"),
            "id_materia":        r.get("IDMateria"),
            "nom_materia":       r.get("NomMateria"),
            "fech_inicio_clases":r.get("FechInicioClases"),
            "fech_fin_clases":   r.get("FechFinClases"),
            "nom_docente":       r.get("NomDocente"),
            "ap_docente":        r.get("ApDocente"),
            "id_docente":        r.get("IDDocente"),
            "mensaje":           r.get("Mensaje"),
        }

        nuevo_hash = calcular_hash(registro)
        registro["row_hash"] = nuevo_hash

        pk = (id_grupo, periodo)
        hash_anterior = hashes_existentes.get(pk)

        if hash_anterior is None:
            res.insertados += 1
        elif hash_anterior != nuevo_hash:
            res.actualizados += 1
        else:
            res.sin_cambios += 1
            continue

        registros_upsert.append(registro)

    if registros_upsert:
        upsert_registros(TABLA, registros_upsert)

    logger.info(
        f"[Section][{periodo}] "
        f"WS={res.registros_ws} new={res.insertados} "
        f"upd={res.actualizados} igual={res.sin_cambios}"
    )
    return res
