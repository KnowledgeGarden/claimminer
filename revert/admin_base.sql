-- Deploy admin_base
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

DROP EXTENSION IF EXISTS pgjwt;
DROP EXTENSION IF EXISTS pgcrypto;
DROP EXTENSION IF EXISTS cube;
DROP EXTENSION IF EXISTS vector;
DROP EXTENSION IF EXISTS plpython3u;
DROP LANGUAGE IF EXISTS plpython3u;

COMMIT;