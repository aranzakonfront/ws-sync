-- ============================================================
-- ws_billing2
-- Billing enriquecido con datos de SAPPO (report.totales_materias_estudiante)
-- Misma PK que ws_billing: (id_pago, codigo_detalle)
-- Solo se llena desde el job nocturno (no hay backfill histórico)
-- ============================================================

CREATE TABLE IF NOT EXISTS ws_billing2 (
    -- Identificadores (misma PK que ws_billing)
    id_pago             TEXT NOT NULL,
    codigo_detalle      TEXT NOT NULL,
    -- Campos del WS (mismos que ws_billing)
    universidad         TEXT,
    campus              TEXT,
    id_transaccion      TEXT,
    periodo             TEXT,
    fech_pago           TEXT,
    fech_pago_date      DATE,
    fech_ini_clases     TEXT,
    id_solicitante      TEXT,
    id_estudiante       TEXT,
    id_persona          INTEGER,
    monto               TEXT,
    cod_descuento       TEXT,
    desc_descuento      TEXT,
    porc_descuento      TEXT,
    no_mat_pagadas      TEXT,
    descripcion_detalle TEXT,
    fecha_transaccion   TEXT,
    codigo              TEXT,
    descripcion         TEXT,
    mensaje             TEXT,
    -- Campos enriquecidos de SAPPO (report.totales_materias_estudiante)
    aprobadas           INTEGER,
    reprobadas          INTEGER,
    cursando            INTEGER,
    -- Campos calculados
    saldo_alumno        TEXT,       -- 'Deuda' si Monto >= 0, 'A favor' si Monto < 0
    alcanza             BOOLEAN,    -- NULL si Deuda; True/False si A favor
    -- SC
    sc                  TEXT,
    -- Control
    row_hash            TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id_pago, codigo_detalle)
);

CREATE INDEX IF NOT EXISTS idx_ws_billing2_fech_pago_date ON ws_billing2 (fech_pago_date);
CREATE INDEX IF NOT EXISTS idx_ws_billing2_sc             ON ws_billing2 (sc);
CREATE INDEX IF NOT EXISTS idx_ws_billing2_id_estudiante  ON ws_billing2 (id_estudiante);
