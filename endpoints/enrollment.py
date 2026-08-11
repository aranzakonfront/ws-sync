"""
endpoints/enrollment.py
Procesa registros del WS /Enrollment.
SC se resuelve por (id_estudiante, CodPrograma).
observaciones se obtiene de SAPPO (report.comparativo_materias_inscritas.tipo_materia)
en batch por (estudiante_id, periodo_id, materia_id). Puede ser NULL.

Detección de eliminados:
  Compara los id_enrollment que vienen del WS esta noche contra los que
  están activos en Supabase para ese periodo. Los que desaparecen se
  marcan como activo=false y se insertan en sync_queue_deletes_enrollment
  para que Bubble los procese.
  Si un enrollment eliminado vuelve a aparecer en el WS, el upsert lo
  reactiva automáticamente (activo=true en el registro).
"""

import logging
from dataclasses import dataclass
from db_sappo import get_tipo_materia_batch
from db_supabase import (
    calcular_hash, get_hashes_existentes,
    upsert_registros, insertar_en_queue,
    get_enrollments_activos_periodo,
    marcar_enrollments_inactivos,
    insertar_deletes_enrollment,
)

logger = logging.getLogger(__name__)
TABLA    = "ws_enrollment"
PK_COLS  = ["id_enrollment", "periodo"]


@dataclass
class ResultadoEndpoint:
    registros_ws: int = 0
    sc_resueltos: int = 0
    insertados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    en_queue: int = 0
    eliminados: int = 0


def procesar(registros_ws: list, sc_por_programa: dict, periodo: str,
             escribir_queue: bool = True) -> ResultadoEndpoint:

    if not registros_ws:
        logger.info(f"[Enrollment][{periodo}] Sin registros del WS")
        return ResultadoEndpoint()

    res = ResultadoEndpoint(registros_ws=len(registros_ws))
    hashes_existentes = get_hashes_existentes(TABLA, PK_COLS)

    # Cargar enrollments activos en Supabase para detección de eliminados
    activos_supabase = get_enrollments_activos_periodo(periodo)

    # --- Batch query a SAPPO para observaciones ---
    # Construir ternas únicas (estudiante_id, periodo_id, materia_id)
    ternas = list({
        (
            str(r.get("IDEstudiante", "")).strip(),
            periodo,
            str(r.get("IDMateria", "")).strip(),
        )
        for r in registros_ws
        if r.get("IDEstudiante") and r.get("IDMateria")
    })
    tipo_materia_map = get_tipo_materia_batch(ternas) if ternas else {}

    registros_upsert = []
    entradas_queue   = []
    ids_en_ws        = set()

    for r in registros_ws:
        id_enrollment = str(r.get("IDEnrollment", "")).strip()
        id_est        = str(r.get("IDEstudiante", "")).strip()
        id_materia    = str(r.get("IDMateria", "")).strip()

        if not id_enrollment:
            logger.warning(f"[Enrollment][{periodo}] Registro sin IDEnrollment, se omite")
            continue

        ids_en_ws.add(id_enrollment)

        cod_programa = str(r.get("CodPrograma", "")).strip()
        sc = sc_por_programa.get((id_est, cod_programa))
        if sc:
            res.sc_resueltos += 1

        # Obtener observaciones desde el mapa de SAPPO (puede ser None)
        observaciones = tipo_materia_map.get((id_est, periodo, id_materia))

        registro = {
            "id_enrollment":  id_enrollment,
            "periodo":        periodo,
            "universidad":    r.get("Universidad"),
            "campus":         r.get("Campus"),
            "id_grupo":       r.get("IDGrupo"),
            "sub_periodo":    r.get("SubPeriodo"),
            "cod_programa":   cod_programa,
            "nom_programa":   r.get("NomPrograma"),
            "id_materia":     id_materia,
            "nom_materia":    r.get("NomMateria"),
            "id_solicitante": r.get("IDSolicitante"),
            "id_estudiante":  id_est,
            "id_persona":     r.get("IDPersona"),
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
            "activo":         True,
            "observaciones":  observaciones,   # NULL si no hay registro en SAPPO
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
        if escribir_queue:
            entradas_queue.append({
                "endpoint":    "enrollment",
                "tipo":        tipo,
                "id_registro": id_enrollment,
                "periodo":     periodo,
                "sc":          sc,
                "payload":     registro,
            })

    # --- Detección de eliminados ---
    ids_eliminados = set(activos_supabase.keys()) - ids_en_ws
    if ids_eliminados:
        logger.info(
            f"[Enrollment][{periodo}] {len(ids_eliminados)} enrollments "
            f"desaparecieron del WS → marcando inactivos"
        )
        marcar_enrollments_inactivos(list(ids_eliminados), periodo)

        entradas_deletes = [
            {
                "id_enrollment": id_enrollment,
                "id_estudiante": activos_supabase[id_enrollment].get("id_estudiante", ""),
                "periodo":       periodo,
                "id_materia":    activos_supabase[id_enrollment].get("id_materia", ""),
            }
            for id_enrollment in ids_eliminados
        ]
        insertar_deletes_enrollment(entradas_deletes)
        res.eliminados = len(ids_eliminados)

    # --- Escribir en Supabase ---
    if registros_upsert:
        upsert_registros(TABLA, registros_upsert)
    if entradas_queue and escribir_queue:
        insertar_en_queue(entradas_queue)
        res.en_queue = len(entradas_queue)

    logger.info(
        f"[Enrollment][{periodo}] "
        f"WS={res.registros_ws} SC={res.sc_resueltos} "
        f"new={res.insertados} upd={res.actualizados} "
        f"igual={res.sin_cambios} eliminados={res.eliminados} queue={res.en_queue}"
    )
    return res
