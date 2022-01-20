-- Deploy document
-- requires: user
-- requires: collection
-- requires: analyzer
-- requires: uri_equiv
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

CREATE TABLE IF NOT EXISTS public.document (
    id bigint NOT NULL DEFAULT nextval('public.topic_id_seq'::regclass),
    url character varying(255) NOT NULL, -- to delete
    uri_id bigint NOT NULL,
    is_archive boolean NOT NULL DEFAULT false,
    requested timestamp with time zone NOT NULL default now(),
    return_code smallint,
    retrieved timestamp without time zone,
    created timestamp without time zone,
    modified timestamp without time zone,
    mimetype character varying(255),
    language character varying(16),
    added_by bigint,
    text_analyzer_id bigint,
    etag varchar(64),
    file_identity char(64),
    file_size integer,
    text_identity char(64),
    text_size integer,
    title text,
    process_params JSONB,
    metadata JSONB default '{}',
    public_contents boolean NOT NULL default true,
    CONSTRAINT document_pkey PRIMARY KEY (id),
    CONSTRAINT document_url_id_key FOREIGN KEY (uri_id)
      REFERENCES uri_equiv (id);
    CONSTRAINT document_added_by_key FOREIGN KEY (added_by)
      REFERENCES public.user (id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT document_text_analyzer_id_key FOREIGN KEY (text_analyzer_id)
      REFERENCES public.analyzer (id) ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS document_uri_id_idx ON public.document (uri_id) WHERE (not is_archive);


CREATE TABLE IF NOT EXISTS public.doc_collection (
  doc_id bigint NOT NULL,
  collection_id bigint NOT NULL,
  CONSTRAINT doc_collection_pkey PRIMARY KEY (doc_id, collection_id),
  CONSTRAINT doc_collection_doc_id FOREIGN KEY (doc_id)
    REFERENCES document (id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT doc_collection_collection_id FOREIGN KEY (collection_id)
    REFERENCES collection (id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS doc_collection_inv_idx ON public.doc_collection (collection_id, doc_id);

COMMIT;
