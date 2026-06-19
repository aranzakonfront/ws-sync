"""
endpoints/applicant.py
Procesa registros del WS /Applicant.
SC se resuelve directo en SAPPO por (id_estudiante, CodPrograma),
igual que Student y Enrollment (resuelto en resolver_sc.py).
"""

import logging
from dataclasses import dataclass
from db_supabase import (
    calcular_hash, get_hashes_existentes,
    upsert_registros, insertar_en_queue
)
from utils import to_int_or_none

logger = logging.getLogger(__name__)
TABLA = "ws_applicant"
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
    if not registros_ws:
        logger.info(f"[Applicant][{periodo}] Sin registros del WS")
        return ResultadoEndpoint()

    res = ResultadoEndpoint(registros_ws=len(registros_ws))
    hashes_existentes = get_hashes_existentes(TABLA, PK_COLS)

    registros_upsert = []
    entradas_queue = []

    for r in registros_ws:
        id_est = str(r.get("IDEstudiante", "")).strip()
        if not id_est:
            logger.warning(f"[Applicant][{periodo}] Registro sin IDEstudiante, se omite")
            continue

        cod_programa = str(r.get("CodPrograma", "")).strip()
        sc = sc_por_programa.get((id_est, cod_programa))
        if sc:
            res.sc_resueltos += 1

        registro = {
            "id_estudiante":     id_est,
            "periodo":           periodo,
            "universidad":       r.get("Universidad"),
            "campus":            r.get("Campus"),
            "id_solicitante":    r.get("IDSolicitante"),
            "id_persona":        to_int_or_none(r.get("IDPersona")),
            "ap_paterno":        r.get("ApPaterno"),
            "ap_materno":        r.get("ApMaterno"),
            "nombre":            r.get("Nombre"),
            "suffix":            r.get("suffix"),
            "direccion_linea1":  r.get("DireccionLinea1"),
            "direccion_linea2":  r.get("DireccionLinea2"),
            "direccion_linea3":  r.get("DireccionLinea3"),
            "ciudad":            r.get("Ciudad"),
            "estado":            r.get("Estado"),
            "pais":              r.get("Pais"),
            "cp":                r.get("CP"),
            "celular":           r.get("Celular"),
            "tel_casa":          r.get("TelCasa"),
            "tel_trabajo":       r.get("TelTrabajo"),
            "email_personal":    r.get("EmailPersonal"),
            "email_institucion": r.get("EmailInstitucion"),
            "nacionalidad":      r.get("Nacionalidad"),
            "periodo_ingreso":   r.get("PeriodoIngreso"),
            "fecha_inicio_clases": r.get("FechaInicioClases"),
            "cod_programa":      r.get("CodPrograma"),
            "cod_edo_sol_adm":   r.get("CodEdoSolAdm"),
            "des_edo_sol_adm":   r.get("DesEdoSolAdm"),
            "fech_sol_adm":      r.get("FechSolAdm"),
            "fech_admision":     r.get("FechAdmision"),
            "curp":              r.get("CURP"),
            "puesto":            r.get("Puesto"),
            "empresa":           r.get("Empresa"),
            "escuela_procedencia": r.get("EscuelaProcedencia"),
            "nom_programa":      r.get("NomPrograma"),
            "sexo":              r.get("Sexo"),
            "fech_nac":          r.get("FechNac"),
            "ano_egreso":        r.get("AnoEgreso"),
            "edo_civil":         r.get("EdoCivil"),
            "promedio":          r.get("Promedio"),
            "mensaje":           r.get("Mensaje"),
            "sc":                sc,
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
            continue

        registros_upsert.append(registro)
        entradas_queue.append({
            "endpoint": "applicant",
            "tipo": tipo,
            "id_registro": id_est,
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
        f"[Applicant][{periodo}] "
        f"WS={res.registros_ws} SC={res.sc_resueltos} "
        f"new={res.insertados} upd={res.actualizados} "
        f"igual={res.sin_cambios} queue={res.en_queue}"
    )
    return res
