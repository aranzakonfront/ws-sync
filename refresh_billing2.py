"""
refresh_billing2.py
Job de refresco para ws_billing2.

Problema que resuelve:
  billing2.procesar() solo recibe los registros del WS /Billing del rango
  "ayer a hoy" (mismo batch que billing normal). Un pago hecho hace varios
  días nunca se vuelve a traer del WS, así que aunque SAPPO actualice
  aprobadas/reprobadas/cursando para ese alumno, ws_billing2 se queda
  con los valores congelados del día en que se creó el registro.

Qué hace:
  1. Lee de ws_billing2 todas las filas con fech_pago_date >= hoy - 30 días
  2. Agrupa por IDEstudiante (sin duplicar llamadas a SAPPO)
  3. Vuelve a consultar SAPPO (report.totales_materias_estudiante) para esos IDs
  4. Recalcula aprobadas/reprobadas/cursando/materias_cargadas/saldo_alumno/alcanza
  5. Recalcula row_hash y hace upsert SOLO si algo cambió

No vuelve a llamar al WS /Billing — los campos del pago en sí (Monto, FechPago,
etc.) no cambian, solo el avance académico del alumno en SAPPO.

Se corre como parte del cron nocturno, después de billing2 normal (ver main.py).
También se puede correr manualmente:
    railway run python refresh_billing2.py
"""

import logging
import time
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("refresh_billing2")

from db_sappo import get_totales_materias_batch
from db_supabase import (
    get_filas_por_fecha, calcular_hash, upsert_registros, registrar_control
)

TABLA = "ws_billing2"
DIAS_VENTANA = 30   # solo refresca pagos de los últimos N días


def _calcular_saldo_y_alcanza(monto_str, precio_neto):
    """Misma lógica que endpoints/billing2.py — mantener sincronizado si cambia ahí."""
    try:
        monto = float(monto_str) if monto_str not in (None, "") else None
    except (ValueError, TypeError):
        monto = None

    if monto is None:
        return None, False
    if monto >= 0:
        return "Deuda", False
    else:
        if precio_neto is not None:
            try:
                alcanza = (abs(monto) - float(precio_neto)) >= 0
                return "A favor", alcanza
            except (ValueError, TypeError):
                pass
        return "A favor", False


def run():
    inicio = time.time()
    fecha_limite = (date.today() - timedelta(days=DIAS_VENTANA)).isoformat()

    logger.info(f"[refresh_billing2] Cargando filas con fech_pago_date >= {fecha_limite}")
    filas = get_filas_por_fecha(TABLA, "fech_pago_date", fecha_limite)
    logger.info(f"[refresh_billing2] {len(filas)} filas en ventana de {DIAS_VENTANA} días")

    if not filas:
        registrar_control(
            endpoint="refresh_billing2", periodo=None,
            registros_ws=0, sc_resueltos=0, insertados=0,
            actualizados=0, sin_cambios=0, en_queue=0,
            status="success", error_msg=None, duracion_seg=time.time() - inicio,
        )
        return

    ids_estudiante = list({
        str(f.get("id_estudiante", "")).strip()
        for f in filas if f.get("id_estudiante")
    })
    logger.info(f"[refresh_billing2] Reconsultando SAPPO para {len(ids_estudiante)} alumnos únicos")

    totales_sappo = get_totales_materias_batch(ids_estudiante)

    actualizados = 0
    sin_cambios = 0
    registros_upsert = []

    for fila in filas:
        id_est = str(fila.get("id_estudiante", "")).strip()
        datos_sappo = totales_sappo.get(id_est, {})

        saldo_alumno, alcanza = _calcular_saldo_y_alcanza(
            fila.get("monto"),
            datos_sappo.get("precio_neto_materia")
        )

        # Reconstruir el registro completo con los campos académicos actualizados
        registro = {k: v for k, v in fila.items() if k not in ("row_hash", "updated_at")}
        registro["aprobadas"]         = datos_sappo.get("aprobadas")
        registro["reprobadas"]        = datos_sappo.get("reprobadas")
        registro["cursando"]          = datos_sappo.get("cursando")
        registro["materias_cargadas"] = datos_sappo.get("materias_cargadas")
        registro["saldo_alumno"]      = saldo_alumno
        registro["alcanza"]           = alcanza

        nuevo_hash = calcular_hash(registro)
        hash_anterior = fila.get("row_hash")

        if nuevo_hash == hash_anterior:
            sin_cambios += 1
            continue

        registro["row_hash"] = nuevo_hash
        registros_upsert.append(registro)
        actualizados += 1

    if registros_upsert:
        upsert_registros(TABLA, registros_upsert, batch_size=100)

    duracion = time.time() - inicio
    logger.info(
        f"[refresh_billing2] Completado en {duracion:.1f}s — "
        f"revisados={len(filas)} actualizados={actualizados} sin_cambios={sin_cambios}"
    )

    registrar_control(
        endpoint="refresh_billing2", periodo=None,
        registros_ws=len(filas), sc_resueltos=0, insertados=0,
        actualizados=actualizados, sin_cambios=sin_cambios, en_queue=0,
        status="success", error_msg=None, duracion_seg=duracion,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run()
