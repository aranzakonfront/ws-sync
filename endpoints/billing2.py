"""
endpoints/billing2.py
Procesa los registros de Billing enriquecidos con datos de SAPPO
(report.totales_materias_estudiante) y los guarda en ws_billing2.

Campos calculados:
  saldo_alumno: 'Deuda' si Monto >= 0, 'A favor' si Monto < 0
  alcanza:      NULL si Deuda;
                True si A favor y abs(Monto) >= precio_neto_materia;
                False si A favor pero abs(Monto) < precio_neto_materia

No escribe en sync_queue_ws (Bubble no consume esta tabla de momento).
Solo se llena desde el job nocturno — no hay backfill histórico.
"""

import logging
from dataclasses import dataclass
from db_sappo import get_totales_materias_batch
from db_supabase import (
    calcular_hash, get_hashes_existentes,
    upsert_registros, parsear_fecha_ws
)
from utils import to_int_or_none

logger = logging.getLogger(__name__)
TABLA = "ws_billing2"
PK_COLS = ["id_pago", "codigo_detalle"]


@dataclass
class ResultadoEndpoint:
    registros_ws: int = 0
    sc_resueltos: int = 0
    sappo_resueltos: int = 0
    insertados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    en_queue: int = 0


def _calcular_saldo_y_alcanza(monto_str, precio_neto):
    """
    Retorna (saldo_alumno, alcanza).
    saldo_alumno: 'Deuda' | 'A favor' | None
    alcanza:      True si A favor y abs(Monto) >= precio_neto_materia
                  False en cualquier otro caso (Deuda, no alcanza, sin precio)
    """
    try:
        monto = float(monto_str) if monto_str not in (None, "") else None
    except (ValueError, TypeError):
        monto = None

    if monto is None:
        return None, False

    if monto >= 0:
        return "Deuda", False
    else:
        # A favor
        if precio_neto is not None:
            try:
                alcanza = (abs(monto) - float(precio_neto)) >= 0
                return "A favor", alcanza
            except (ValueError, TypeError):
                pass
        return "A favor", False


def procesar(registros_ws: list, sc_por_estudiante: dict,
             escribir_queue: bool = False) -> ResultadoEndpoint:
    """
    Args:
        registros_ws:       lista cruda del WS /Billing (misma que procesa billing.py)
        sc_por_estudiante:  {id_estudiante: SC} resuelto globalmente esa noche
        escribir_queue:     siempre False por ahora (Bubble no consume esto)
    """
    if not registros_ws:
        logger.info("[Billing2] Sin registros del WS")
        return ResultadoEndpoint()

    # Deduplicar por (id_pago, codigo_detalle) igual que billing.py
    total_original = len(registros_ws)
    vistos = {}
    for r in registros_ws:
        id_pago = str(r.get("IDPago", "")).strip()
        cod_detalle = str(r.get("CODIGO_DETALLE", "")).strip()
        if id_pago and cod_detalle:
            vistos[(id_pago, cod_detalle)] = r
    registros_ws = list(vistos.values())
    if len(registros_ws) < total_original:
        logger.info(f"[Billing2] {total_original - len(registros_ws)} duplicados eliminados")

    res = ResultadoEndpoint(registros_ws=len(registros_ws))

    # Consultar SAPPO en batch para todos los IDEstudiante únicos
    ids_estudiante = list({
        str(r.get("IDEstudiante", "")).strip()
        for r in registros_ws if r.get("IDEstudiante")
    })
    totales_sappo = get_totales_materias_batch(ids_estudiante)
    res.sappo_resueltos = len(totales_sappo)

    hashes_existentes = get_hashes_existentes(TABLA, PK_COLS)
    registros_upsert = []

    for r in registros_ws:
        id_pago = str(r.get("IDPago", "")).strip()
        cod_detalle = str(r.get("CODIGO_DETALLE", "")).strip()
        id_est = str(r.get("IDEstudiante", "")).strip()

        if not id_pago or not cod_detalle:
            continue

        sc = sc_por_estudiante.get(id_est)
        if sc:
            res.sc_resueltos += 1

        datos_sappo = totales_sappo.get(id_est, {})
        saldo_alumno, alcanza = _calcular_saldo_y_alcanza(
            r.get("Monto"),
            datos_sappo.get("precio_neto_materia")
        )

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
            # Enriquecimiento SAPPO
            "aprobadas":          datos_sappo.get("aprobadas"),
            "reprobadas":         datos_sappo.get("reprobadas"),
            "cursando":           datos_sappo.get("cursando"),
            "materias_cargadas":  datos_sappo.get("materias_cargadas"),
            # Calculados
            "saldo_alumno":       saldo_alumno,
            "alcanza":            alcanza,
            "sc":                 sc,
        }

        nuevo_hash = calcular_hash(registro)
        registro["row_hash"] = nuevo_hash

        pk = (id_pago, cod_detalle)
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
        upsert_registros(TABLA, registros_upsert, batch_size=100)

    logger.info(
        f"[Billing2] WS={res.registros_ws} SC={res.sc_resueltos} "
        f"SAPPO={res.sappo_resueltos} "
        f"new={res.insertados} upd={res.actualizados} "
        f"igual={res.sin_cambios}"
    )
    return res
