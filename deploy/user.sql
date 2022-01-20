-- Deploy user
-- requires: base
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

CREATE TABLE IF NOT EXISTS public.user (
    id bigint NOT NULL DEFAULT nextval('public.topic_id_seq'::regclass),
    email character varying(255) NOT NULL,
    handle character varying(255) NOT NULL,
    passwd character varying(255) NOT NULL,
    confirmed boolean DEFAULT false,
    created timestamp without time zone NOT NULL default now(),
    permissions public.permission[] DEFAULT ARRAY[]::public.permission[],
    CONSTRAINT user_pkey PRIMARY KEY (id),
    CONSTRAINT user_handle_key UNIQUE (handle),
    CONSTRAINT user_email_key UNIQUE (email)
);

COMMIT;
