-- Deploy analyzer
-- requires: base
-- Copyright Society Library and Conversence 2022-2023

BEGIN;


CREATE TABLE IF NOT EXISTS public.analyzer (
    id bigint NOT NULL DEFAULT nextval('public.topic_id_seq'::regclass),
    name character varying(100) NOT NULL,
    nickname character varying,
    version smallint NOT NULL,
    draft BOOLEAN NOT NULL DEFAULT false,
    params JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT analyzer_pkey PRIMARY KEY (id),
    CONSTRAINT analyzer_name_version_params_unique_key UNIQUE (name, version, params),
    CONSTRAINT analyzer_name_nickname_unique_key UNIQUE (name, nickname)
);

COMMIT;
