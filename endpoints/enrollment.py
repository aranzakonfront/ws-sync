"""
endpoints/enrollment.py
Procesa registros del WS /Enrollment.
SC se resuelve por (id_estudiante, CodPrograma).
"""

import logging
from dataclasses import dataclass
from db_supabase import (
    calcular_hash, get_hashes_existentes,
    upsert_registros, insertar_en_queue
)
from utils import to_int_or_none

logger = logging.getLogger(__name__)
TABLA = "ws_enrollment"
PK_COLS = ["id_enrollment", "periodo"]


@dataclass
class ResultadoEndpoint:
    registros_ws: int = 0
    sc_resueltos: int = 0
    insertados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    en_queue: int = 0


def procesar(registros_ws: list, sc_por_programa: dict, periodo: str) -> ResultadoEndpoint:
    if not registros_ws:
        logger.info(f"[Enrollment][{periodo}] Sin registros del WS")
        return ResultadoEndpoint()

    res = ResultadoEndpoint(registros_ws=len(registros_ws))
    hashes_existentes = get_hashes_existentes(TABLA, PK_COLS)

    registros_upsert = []
    entradas_queue = []

    for r in registros_ws:
        id_enrollment = str(r.get("IDEnrollment", "")).strip()
        id_est = str(r.get("IDEstudiante", "")).strip()

        if not id_enrollment:
            logger.warning(f"[Enrollment][{periodo}] Registro sin IDEnrollment, se omite")
            continue

        cod_programa = str(r.get("CodPrograma", "")).strip()
        sc = sc_por_programa.get((id_est, cod_programa))
        if sc:
            res.sc_resueltos += 1

        registro = {
            "id_enrollment":  id_enrollment,
            "periodo":        periodo,
            "universidad":    r.get("Universidad"),
            "campus":         r.get("Campus"),
            "id_grupo":       r.get("IDGrupo"),
            "sub_periodo":    r.get("SubPeriodo"),
            "cod_programa":   r.get("CodPrograma"),
            "nom_programa":   r.get("NomPrograma"),
            "id_materia":     r.get("IDMateria"),
            "nom_materia":    r.get("NomMateria"),
            "id_solicitante": r.get("IDSolicitante"),
            "id_estudiante":  id_est,
            "id_persona":     to_int_or_none(r.get("IDPersona")),
            "fech_ini_clases":r.get("FechIniClases"),
            "fech_fin_clases":r.get("FechFinClases"),
            "cod_edo_mat":    r.get("CodEdoMat"),
            "desc_edo_mat":   r.get("DescEdoMat"),
            "razon_estado":   r.get("RazonEstado"),
            "fech_ins_mat":   r.get("FechInsMat"),
            "calificacion":   r.get("Calificación"),
            "aprobado":       r.get("Aprobado"),
            "mensaje":        r.get("Mensaje"),
            "sc":             sc,
        }

        nuevo_hash = calcular_hash(registro)
        registro["row_hash"] = nuevo_hash

        pk = (id_enrollment, periodo)
        hash_anterior = hashes_existentes.get(pk)

        if hash_anterior is None:
            tipo = "created"
            res.insertados += 1
        elif hash_anterior != nuevo_hash:
            tipo = "updated"
            res.actualizados += 1
        else:
            res.sin_cambios += 1
            continue

        registros_upsert.append(registro)
        entradas_queue.append({
            "endpoint": "enrollment",
            "tipo": tipo,
            "id_registro": id_enrollment,
            "periodo": periodo,
            "sc": sc,
            "payload": registro,
        })

    if registros_upsert:
        upsert_registros(TABLA, registros_upsert)
    if entradas_queue:
        insertar_en_queue(entradas_queue)
        res.en_queue = len(entradas_queue)

    logger.info(
        f"[Enrollment][{periodo}] "
        f"WS={res.registros_ws} SC={res.sc_resueltos} "
        f"new={res.insertados} upd={res.actualizados} "
        f"igual={res.sin_cambios} queue={res.en_queue}"
    )
    return res
