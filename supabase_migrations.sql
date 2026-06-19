-- ============================================================
-- WS SYNC - Migraciones Supabase
-- Ejecutar en orden en el SQL Editor de Supabase
-- ============================================================

-- ============================================================
-- 1. ws_student
-- ============================================================
CREATE TABLE IF NOT EXISTS ws_student (
    -- Identificadores
    id_estudiante       TEXT NOT NULL,
    periodo             TEXT NOT NULL,
    -- Datos del WS
    universidad         TEXT,
    campus              TEXT,
    id_solicitante      TEXT,
    id_persona          INTEGER,
    ap_paterno          TEXT,
    ap_materno          TEXT,
    nombre              TEXT,
    suffix              TEXT,
    direccion_linea1    TEXT,
    direccion_linea2    TEXT,
    direccion_linea3    TEXT,
    ciudad              TEXT,
    estado              TEXT,
    pais                TEXT,
    cp                  TEXT,
    celular             TEXT,
    tel_casa            TEXT,
    tel_trabajo         TEXT,
    email_personal      TEXT,
    email_institucion   TEXT,
    nacionalidad        TEXT,
    cohorte             TEXT,
    fecha_inicio_clases_cohorte TEXT,
    fech_admision       TEXT,
    curp                TEXT,
    puesto              TEXT,
    empresa             TEXT,
    escuela_procedencia TEXT,
    cod_programa        TEXT,
    nom_programa        TEXT,
    sexo                TEXT,
    fech_nac            TEXT,
    ano_egreso          TEXT,
    edo_civil           TEXT,
    promedio            TEXT,
    cod_edo_alumno      TEXT,
    desc_edo_alumno     TEXT,
    fech_edo_alumno     TEXT,
    fech_graduacion     TEXT,
    mensaje             TEXT,
    -- Campo SC (Socio Comercial - obtenido de SAPPO)
    sc                  TEXT,
    -- Control
    row_hash            TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id_estudiante, periodo)
);

CREATE INDEX IF NOT EXISTS idx_ws_student_periodo     ON ws_student (periodo);
CREATE INDEX IF NOT EXISTS idx_ws_student_sc          ON ws_student (sc);
CREATE INDEX IF NOT EXISTS idx_ws_student_id_banner   ON ws_student (id_estudiante);
CREATE INDEX IF NOT EXISTS idx_ws_student_cod_prog    ON ws_student (cod_programa);
CREATE INDEX IF NOT EXISTS idx_ws_student_cod_nivel   ON ws_student (cod_programa);  -- filtra prefijo LL/ML/EL


-- ============================================================
-- 2. ws_enrollment
-- ============================================================
CREATE TABLE IF NOT EXISTS ws_enrollment (
    -- Identificadores
    id_enrollment       TEXT NOT NULL,
    periodo             TEXT NOT NULL,
    -- Datos del WS
    universidad         TEXT,
    campus              TEXT,
    id_grupo            TEXT,
    sub_periodo         TEXT,
    cod_programa        TEXT,
    nom_programa        TEXT,
    id_materia          TEXT,
    nom_materia         TEXT,
    id_solicitante      TEXT,
    id_estudiante       TEXT,
    id_persona          INTEGER,
    fech_ini_clases     TEXT,
    fech_fin_clases     TEXT,
    cod_edo_mat         TEXT,
    desc_edo_mat        TEXT,
    razon_estado        TEXT,
    fech_ins_mat        TEXT,
    calificacion        TEXT,
    aprobado            TEXT,
    mensaje             TEXT,
    -- Campo SC
    sc                  TEXT,
    -- Control
    row_hash            TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id_enrollment, periodo)
);

CREATE INDEX IF NOT EXISTS idx_ws_enrollment_periodo       ON ws_enrollment (periodo);
CREATE INDEX IF NOT EXISTS idx_ws_enrollment_sc            ON ws_enrollment (sc);
CREATE INDEX IF NOT EXISTS idx_ws_enrollment_id_estudiante ON ws_enrollment (id_estudiante);
CREATE INDEX IF NOT EXISTS idx_ws_enrollment_cod_programa  ON ws_enrollment (cod_programa);


-- ============================================================
-- 3. ws_applicant
-- ============================================================
CREATE TABLE IF NOT EXISTS ws_applicant (
    -- Identificadores
    id_estudiante       TEXT NOT NULL,
    periodo             TEXT NOT NULL,
    -- Datos del WS
    universidad         TEXT,
    campus              TEXT,
    id_solicitante      TEXT,
    id_persona          INTEGER,
    ap_paterno          TEXT,
    ap_materno          TEXT,
    nombre              TEXT,
    suffix              TEXT,
    direccion_linea1    TEXT,
    direccion_linea2    TEXT,
    direccion_linea3    TEXT,
    ciudad              TEXT,
    estado              TEXT,
    pais                TEXT,
    cp                  TEXT,
    celular             TEXT,
    tel_casa            TEXT,
    tel_trabajo         TEXT,
    email_personal      TEXT,
    email_institucion   TEXT,
    nacionalidad        TEXT,
    periodo_ingreso     TEXT,
    fecha_inicio_clases TEXT,
    cod_programa        TEXT,
    cod_edo_sol_adm     TEXT,
    des_edo_sol_adm     TEXT,
    fech_sol_adm        TEXT,
    fech_admision       TEXT,
    curp                TEXT,
    puesto              TEXT,
    empresa             TEXT,
    escuela_procedencia TEXT,
    nom_programa        TEXT,
    sexo                TEXT,
    fech_nac            TEXT,
    ano_egreso          TEXT,
    edo_civil           TEXT,
    promedio            TEXT,
    mensaje             TEXT,
    -- Campo SC (obtenido de Bubble con fallback a SAPPO)
    sc                  TEXT,
    -- Control
    row_hash            TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id_estudiante, periodo)
);

CREATE INDEX IF NOT EXISTS idx_ws_applicant_periodo      ON ws_applicant (periodo);
CREATE INDEX IF NOT EXISTS idx_ws_applicant_sc           ON ws_applicant (sc);
CREATE INDEX IF NOT EXISTS idx_ws_applicant_cod_programa ON ws_applicant (cod_programa);


-- ============================================================
-- 4. ws_section  (sin SC)
-- ============================================================
CREATE TABLE IF NOT EXISTS ws_section (
    -- Identificadores
    id_grupo            TEXT NOT NULL,
    periodo             TEXT NOT NULL,
    -- Datos del WS
    universidad         TEXT,
    campus              TEXT,
    sub_periodo         TEXT,
    id_materia          TEXT,
    nom_materia         TEXT,
    fech_inicio_clases  TEXT,
    fech_fin_clases     TEXT,
    nom_docente         TEXT,
    ap_docente          TEXT,
    id_docente          TEXT,
    mensaje             TEXT,
    -- Control
    row_hash            TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id_grupo, periodo)
);

CREATE INDEX IF NOT EXISTS idx_ws_section_periodo ON ws_section (periodo);


-- ============================================================
-- 5. ws_billing  (acumula histórico, sin periodo como PK)
-- ============================================================
CREATE TABLE IF NOT EXISTS ws_billing (
    -- Identificadores (un pago puede tener varios detalles)
    id_pago             TEXT NOT NULL,
    codigo_detalle      TEXT NOT NULL,
    -- Datos del WS
    universidad         TEXT,
    campus              TEXT,
    id_transaccion      TEXT,
    periodo             TEXT,
    fech_pago           TEXT,           -- "dd/mm/yyyy" tal como viene del WS
    fech_pago_date      DATE,           -- campo derivado para filtrar eficientemente
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
    -- Campo SC
    sc                  TEXT,
    -- Control
    row_hash            TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id_pago, codigo_detalle)
);

CREATE INDEX IF NOT EXISTS idx_ws_billing_fech_pago_date ON ws_billing (fech_pago_date);
CREATE INDEX IF NOT EXISTS idx_ws_billing_sc             ON ws_billing (sc);
CREATE INDEX IF NOT EXISTS idx_ws_billing_id_estudiante  ON ws_billing (id_estudiante);
CREATE INDEX IF NOT EXISTS idx_ws_billing_id_banner      ON ws_billing (id_estudiante);
CREATE INDEX IF NOT EXISTS idx_ws_billing_periodo        ON ws_billing (periodo);


-- ============================================================
-- 6. sync_queue_ws  (cola de cambios para Bubble)
-- ============================================================
CREATE TABLE IF NOT EXISTS sync_queue_ws (
    id              BIGSERIAL PRIMARY KEY,
    endpoint        TEXT NOT NULL,      -- 'student' | 'enrollment' | 'billing' | 'applicant' | 'section'
    tipo            TEXT NOT NULL,      -- 'created' | 'updated'
    id_registro     TEXT NOT NULL,      -- IDEstudiante, IDEnrollment, IDPago+CodigoDetalle, etc.
    periodo         TEXT,               -- null para billing
    sc              TEXT,
    payload         JSONB,              -- registro completo para que Bubble no tenga que buscar
    status          TEXT DEFAULT 'pending',   -- 'pending' | 'completed' | 'error'
    created_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_ws_status   ON sync_queue_ws (status);
CREATE INDEX IF NOT EXISTS idx_sync_queue_ws_endpoint ON sync_queue_ws (endpoint);


-- ============================================================
-- 7. sync_control_ws  (log de cada corrida)
-- ============================================================
CREATE TABLE IF NOT EXISTS sync_control_ws (
    id              BIGSERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ DEFAULT now(),
    endpoint        TEXT NOT NULL,
    periodo         TEXT,               -- null para billing
    registros_ws    INTEGER DEFAULT 0,
    sc_resueltos    INTEGER DEFAULT 0,
    insertados      INTEGER DEFAULT 0,
    actualizados    INTEGER DEFAULT 0,
    sin_cambios     INTEGER DEFAULT 0,
    en_queue        INTEGER DEFAULT 0,
    status          TEXT NOT NULL,      -- 'success' | 'error' | 'partial'
    error_msg       TEXT,
    duracion_seg    FLOAT
);

CREATE INDEX IF NOT EXISTS idx_sync_control_ws_run_at   ON sync_control_ws (run_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_control_ws_endpoint ON sync_control_ws (endpoint);
CREATE INDEX IF NOT EXISTS idx_sync_control_ws_status   ON sync_control_ws (status);
