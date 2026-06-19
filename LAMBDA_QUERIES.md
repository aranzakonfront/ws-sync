# Lambda → Supabase: Referencia de queries

Todos los endpoints filtran por SC = 'AP' automáticamente.
La Lambda consulta la API REST de Supabase (PostgREST) vía HTTP.

Base URL: https://<tu-proyecto>.supabase.co/rest/v1/

Headers requeridos:
  apikey: <SUPABASE_ANON_KEY>
  Authorization: Bearer <SUPABASE_ANON_KEY>

## /Student
Params: periodo (obligatorio), id_banner, codigo_programa, codigo_nivel (opcionales)

GET /ws_student?
  periodo=eq.{periodo}
  &sc=eq.AP
  [&id_estudiante=eq.{id_banner}]
  [&cod_programa=eq.{codigo_programa}]
  [&cod_programa=like.{codigo_nivel}%]   ← prefijo: LL, ML o EL

Ejemplo:
  /ws_student?periodo=eq.202592&sc=eq.AP&cod_programa=like.LL%


## /Enrollment
Params: periodo (obligatorio), id_banner, codigo_programa, codigo_nivel (opcionales)

GET /ws_enrollment?
  periodo=eq.{periodo}
  &sc=eq.AP
  [&id_estudiante=eq.{id_banner}]
  [&cod_programa=eq.{codigo_programa}]
  [&cod_programa=like.{codigo_nivel}%]

Ejemplo:
  /ws_enrollment?periodo=eq.202592&sc=eq.AP


## /Applicant
Params: periodo (obligatorio), codigo_programa, codigo_nivel (opcionales)

GET /ws_applicant?
  periodo=eq.{periodo}
  &sc=eq.AP
  [&cod_programa=eq.{codigo_programa}]
  [&cod_programa=like.{codigo_nivel}%]


## /Section
Params: periodo (obligatorio), codigo_nivel (opcionales)
Nota: Section no tiene SC; se devuelven todos los registros del periodo.

GET /ws_section?
  periodo=eq.{periodo}
  [&cod_programa=like.{codigo_nivel}%]


## /Billing
Params: fecha_inicio, fecha_fin (obligatorios), id_banner, codigo_nivel (opcionales)
Nota: filtrar por fech_pago_date (columna DATE, indexada).

GET /ws_billing?
  fech_pago_date=gte.{fecha_inicio_iso}
  &fech_pago_date=lte.{fecha_fin_iso}
  &sc=eq.AP
  [&id_estudiante=eq.{id_banner}]
  [&cod_nivel=like.{codigo_nivel}%]

IMPORTANTE: fecha_inicio y fecha_fin deben enviarse en formato ISO (YYYY-MM-DD).
Si la Lambda recibe dd/mm/yyyy, convertir antes de la query.

Ejemplo:
  /ws_billing?fech_pago_date=gte.2026-06-17&fech_pago_date=lte.2026-06-18&sc=eq.AP


## Paginación
Supabase PostgREST devuelve máx 1000 filas por defecto.
Si hay más registros, usar:
  Range: bytes=0-999   (header)
  o parámetro: &limit=1000&offset=0
