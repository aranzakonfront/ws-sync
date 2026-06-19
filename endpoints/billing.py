"""
endpoints/billing.py
Procesa registros del WS /Billing.
No usa periodos: consulta por fecha_inicio=ayer, fecha_fin=hoy.
Acumula histórico en ws_billing (upsert por IDPago+CodigoDetalle).

SC: Billing no trae CodPrograma en el response del WS, por lo que se usa
el mapa aplanado {id_estudiante: SC} construido en resolver_sc.py, que
reutiliza el SC ya resuelto en Student/Enrollment/Applicant de la misma
corrida (o el fallback sin programa si el alumno no aparece en ninguno).
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from db_supabase import (
    calcular_hash, get_hashes_existentes,
    upsert_registros, insertar_en_queue, parsear_fecha_ws
)
from utils import to_int_or_none

logger = logging.getLogger(__name__)
TABLA = "ws_billing"
PK_COLS = ["id_pago", "codigo_detalle"]


@dataclass
class ResultadoEndpoint:
    registros_ws: int = 0
    sc_resueltos: int = 0
    insertados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    en_queue: int = 0


def get_params_fecha() -> dict:
    """
    Retorna los params de fecha para la llamada al WS.
    fecha_inicio = ayer, fecha_fin = hoy, en formato dd/mm/yyyy.
    """
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    return {
        "fecha_inicio": ayer.strftime("%d/%m/%Y"),
        "fecha_fin": hoy.strftime("%d/%m/%Y"),
    }


def procesar(registros_ws: list, sc_por_estudiante: dict) -> ResultadoEndpoint:
    """
    Nota: Billing no recibe `periodo` como parámetro ya que usa rango de fechas.
    El campo `periodo` en los registros del WS es parte del payload del pago.
    """
    if not registros_ws:
        logger.info("[Billing] Sin registros del WS")
        return ResultadoEndpoint()

    res = ResultadoEndpoint(registros_ws=len(registros_ws))

    # Solo cargar hashes de los IDPago presentes en este batch
    # (billing puede tener millones de registros históricos)
    ids_pago_batch = list({str(r.get("IDPago", "")) for r in registros_ws if r.get("IDPago")})
    hashes_existentes = _get_hashes_batch(ids_pago_batch)

    registros_upsert = []
    entradas_queue = []

    for r in registros_ws:
        id_pago = str(r.get("IDPago", "")).strip()
        cod_detalle = str(r.get("CODIGO_DETALLE", "")).strip()
        id_est = str(r.get("IDEstudiante", "")).strip()

        if not id_pago or not cod_detalle:
            logger.warning("[Billing] Registro sin IDPago o CODIGO_DETALLE, se omite")
            continue

        sc = sc_por_estudiante.get(id_est)
        if sc:
            res.sc_resueltos += 1

        # Parsear FechPago a DATE para indexado eficiente en Supabase
        fech_pago_str = r.get("FechPago")
        fech_pago_date = parsear_fecha_ws(fech_pago_str)

        registro = {
            "id_pago":            id_pago,
            "codigo_detalle":     cod_detalle,
            "universidad":        r.get("Universidad"),
            "campus":             r.get("Campus"),
            "id_transaccion":     r.get("IDTransaccion"),
            "periodo":            r.get("Periodo"),
            "fech_pago":          fech_pago_str,
            "fech_pago_date":     fech_pago_date.isoformat() if fech_pago_date else None,
            "fech_ini_clases":    r.get("FechIniClases"),
            "id_solicitante":     r.get("IDSolicitante"),
            "id_estudiante":      id_est,
            "id_persona":         to_int_or_none(r.get("IDPersona")),
            "monto":              r.get("Monto"),
            "cod_descuento":      r.get("CodDescuento"),
            "desc_descuento":     r.get("DescDescuento"),
            "porc_descuento":     r.get("PorcDescuento"),
            "no_mat_pagadas":     r.get("NoMatPagadas"),
            "descripcion_detalle":r.get("DESCRIPCION_DETALLE"),
            "fecha_transaccion":  r.get("FECHA_TRANSACCION"),
            "codigo":             r.get("CODIGO"),
            "descripcion":        r.get("DESCRIPCION"),
            "mensaje":            r.get("Mensaje"),
            "sc":                 sc,
        }

        nuevo_hash = calcular_hash(registro)
        registro["row_hash"] = nuevo_hash

        pk = (id_pago, cod_detalle)
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
            "endpoint": "billing",
            "tipo": tipo,
            "id_registro": f"{id_pago}::{cod_detalle}",
            "periodo": r.get("Periodo"),
            "sc": sc,
            "payload": registro,
        })

    if registros_upsert:
        upsert_registros(TABLA, registros_upsert)
    if entradas_queue:
        insertar_en_queue(entradas_queue)
        res.en_queue = len(entradas_queue)

    logger.info(
        f"[Billing] WS={res.registros_ws} SC={res.sc_resueltos} "
        f"new={res.insertados} upd={res.actualizados} "
        f"igual={res.sin_cambios} queue={res.en_queue}"
    )
    return res


def _get_hashes_batch(ids_pago: list[str]) -> dict[tuple, str]:
    """
    Carga hashes solo de los IDPago presentes en el batch actual,
    evitando cargar todo el histórico de billing.
    """
    if not ids_pago:
        return {}

    from db_supabase import get_client
    client = get_client()
    resultado = {}

    # Filtrar solo los IDPago del batch
    resp = (
        client.table(TABLA)
        .select("id_pago,codigo_detalle,row_hash")
        .in_("id_pago", ids_pago)
        .execute()
    )
    for fila in resp.data or []:
        pk = (fila["id_pago"], fila["codigo_detalle"])
        resultado[pk] = fila.get("row_hash")

    return resultado
