-- Deploy fragment
-- requires: document
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

CREATE TABLE IF NOT EXISTS public.fragment (
    id bigint NOT NULL DEFAULT nextval('public.topic_id_seq'::regclass),
    doc_id bigint,
    part_of bigint,
    position integer NOT NULL,
    char_position integer NOT NULL,
    scale public.fragment_type NOT NULL,
    language character varying(16) NOT NULL,
    text text NOT NULL,
    created_by bigint,
    external_id character varying,
    analysis_id bigint,
    generation_data JSONB,
    confirmed boolean NOT NULL default true,

    CONSTRAINT fragment_pkey PRIMARY KEY (id),
    CONSTRAINT fragment_doc_id_key FOREIGN KEY (doc_id)
      REFERENCES public.document (id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fragment_part_of_key FOREIGN KEY (part_of)
      REFERENCES public.fragment (id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fragment_analysis_key FOREIGN KEY (analysis_id)
      REFERENCES public.analysis (id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT fragment_created_by_key FOREIGN KEY (created_by)
      REFERENCES public.user (id) ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX fragment_doc_id_idx on fragment (doc_id);
CREATE INDEX fragment_part_of_idx on fragment (part_of);
CREATE INDEX fragment_text_hash_idx on fragment using hash (text);
CREATE UNIQUE INDEX fragment_text_hash_u_idx on fragment (text, coalesce(external_id, '')) WHERE doc_id IS NULL;
-- CREATE INDEX fragment_text_idx on fragment (to_tsvector(text)) using gin;
CREATE INDEX fragment_text_en_idx on fragment using gin (to_tsvector('english', text)) WHERE starts_with(language, 'en');
CREATE UNIQUE INDEX fragment_external_id_idx on fragment (external_id);


CREATE TABLE IF NOT EXISTS public.fragment_collection (
  fragment_id bigint NOT NULL,
  collection_id bigint NOT NULL,
  CONSTRAINT fragment_collection_pkey PRIMARY KEY (fragment_id, collection_id),
  CONSTRAINT fragment_collection_fragment_id FOREIGN KEY (fragment_id)
    REFERENCES fragment (id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fragment_collection_collection_id FOREIGN KEY (collection_id)
    REFERENCES collection (id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS fragment_collection_inv_idx ON public.fragment_collection (collection_id, fragment_id);


COMMIT;
