"""
endpoints/student.py
Procesa los registros del WS /Student:
  1. Mapea los campos del WS al esquema de ws_student
  2. Enriquece con SC (resuelto por (id_estudiante, CodPrograma) en batch)
  3. Calcula row_hash y compara contra Supabase
  4. Genera upsert + entradas para sync_queue_ws
"""

import logging
from dataclasses import dataclass, field
from db_supabase import (
    calcular_hash, get_hashes_existentes,
    upsert_registros, insertar_en_queue
)

logger = logging.getLogger(__name__)
TABLA = "ws_student"
PK_COLS = ["id_estudiante", "periodo"]


@dataclass
class ResultadoEndpoint:
    registros_ws: int = 0
    sc_resueltos: int = 0
    insertados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    en_queue: int = 0


def procesar(registros_ws: list, sc_por_programa: dict, periodo: str) -> ResultadoEndpoint:
    """
    Procesa todos los registros de Student para un periodo dado.

    Args:
        registros_ws:     lista de dicts tal como los devuelve el WS
        sc_por_programa:  {(id_estudiante, programa_id): SC} resuelto globalmente
        periodo:          ID del periodo (ej: '202592')

    Returns:
        ResultadoEndpoint con métricas de la corrida
    """
    if not registros_ws:
        logger.info(f"[Student][{periodo}] Sin registros del WS")
        return ResultadoEndpoint()

    res = ResultadoEndpoint(registros_ws=len(registros_ws))

    # --- 1. Cargar hashes existentes en Supabase ---
    hashes_existentes = get_hashes_existentes(TABLA, PK_COLS)

    # --- 2. Mapear + enriquecer + calcular hash ---
    registros_upsert = []
    entradas_queue = []

    for r in registros_ws:
        id_est = str(r.get("IDEstudiante", "")).strip()
        if not id_est:
            logger.warning(f"[Student][{periodo}] Registro sin IDEstudiante, se omite")
            continue

        cod_programa = str(r.get("CodPrograma", "")).strip()
        sc = sc_por_programa.get((id_est, cod_programa))
        if sc:
            res.sc_resueltos += 1

        registro = {
            "id_estudiante":          id_est,
            "periodo":                periodo,
            "universidad":            r.get("Universidad"),
            "campus":                 r.get("Campus"),
            "id_solicitante":         r.get("IDSolicitante"),
            "id_persona":             r.get("IDPersona"),
            "ap_paterno":             r.get("ApPaterno"),
            "ap_materno":             r.get("ApMaterno"),
            "nombre":                 r.get("Nombre"),
            "suffix":                 r.get("suffix"),
            "direccion_linea1":       r.get("DireccionLinea1"),
            "direccion_linea2":       r.get("DireccionLinea2"),
            "direccion_linea3":       r.get("DireccionLinea3"),
            "ciudad":                 r.get("Ciudad"),
            "estado":                 r.get("Estado"),
            "pais":                   r.get("Pais"),
            "cp":                     r.get("CP"),
            "celular":                r.get("Celular"),
            "tel_casa":               r.get("TelCasa"),
            "tel_trabajo":            r.get("TelTrabajo"),
            "email_personal":         r.get("EmailPersonal"),
            "email_institucion":      r.get("EmailInstitucion") or r.get("EmailInstitucional"),
            "nacionalidad":           r.get("Nacionalidad"),
            "cohorte":                r.get("Cohorte"),
            "fecha_inicio_clases_cohorte": r.get("FechaInicioClasesCohorte"),
            "fech_admision":          r.get("FechAdmision"),
            "curp":                   r.get("CURP"),
            "puesto":                 r.get("Puesto"),
            "empresa":                r.get("Empresa"),
            "escuela_procedencia":    r.get("EscuelaProcedencia"),
            "cod_programa":           r.get("CodPrograma"),
            "nom_programa":           r.get("NomPrograma"),
            "sexo":                   r.get("Sexo"),
            "fech_nac":               r.get("FechNac"),
            "ano_egreso":             r.get("AnoEgreso"),
            "edo_civil":              r.get("EdoCivil"),
            "promedio":               r.get("Promedio"),
            "cod_edo_alumno":         r.get("CodEdoAlumno"),
            "desc_edo_alumno":        r.get("DescEdoAlumno"),
            "fech_edo_alumno":        r.get("FechEdoAlumno"),
            "fech_graduacion":        r.get("FechGraduacion"),
            "mensaje":                r.get("Mensaje"),
            "sc":                     sc,
        }

        nuevo_hash = calcular_hash(registro)
        registro["row_hash"] = nuevo_hash

        pk = (id_est, periodo)
        hash_anterior = hashes_existentes.get(pk)

        if hash_anterior is None:
            tipo = "created"
            res.insertados += 1
        elif hash_anterior != nuevo_hash:
            tipo = "updated"
            res.actualizados += 1
        else:
            res.sin_cambios += 1
            continue   # Sin cambio, no hace upsert ni queue

        registros_upsert.append(registro)
        entradas_queue.append({
            "endpoint": "student",
            "tipo": tipo,
            "id_registro": id_est,
            "periodo": periodo,
            "sc": sc,
            "payload": registro,
        })

    # --- 3. Escribir en Supabase ---
    if registros_upsert:
        upsert_registros(TABLA, registros_upsert)
    if entradas_queue:
        insertar_en_queue(entradas_queue)
        res.en_queue = len(entradas_queue)

    logger.info(
        f"[Student][{periodo}] "
        f"WS={res.registros_ws} SC={res.sc_resueltos} "
        f"new={res.insertados} upd={res.actualizados} "
        f"igual={res.sin_cambios} queue={res.en_queue}"
    )
    return res
