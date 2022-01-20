-- Deploy collection
-- requires: user
-- Copyright Society Library and Conversence 2022-2023

BEGIN;


CREATE TABLE IF NOT EXISTS public.collection (
    id bigint NOT NULL DEFAULT nextval('public.topic_id_seq'::regclass) PRIMARY KEY,
    name varchar NOT NULL
);

CREATE UNIQUE INDEX collection_name_idx on collection (name);

CREATE TABLE IF NOT EXISTS public.collection_permissions (
  user_id bigint NOT NULL,
  collection_id bigint NOT NULL,
  permissions public.permission[] DEFAULT ARRAY[]::public.permission[],
  params JSONB NOT NULL DEFAULT '{}'::JSONB,
  CONSTRAINT pcollection_pkey PRIMARY KEY (user_id, collection_id),
  CONSTRAINT collection_permissions_user_id_key FOREIGN KEY (user_id)
    REFERENCES public.user (id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT collection_permissions_collection_id_key FOREIGN KEY (collection_id)
    REFERENCES public.collection (id) ON DELETE CASCADE ON UPDATE CASCADE
);


COMMIT;
