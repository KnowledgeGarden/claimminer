-- Deploy embedding
-- requires: fragment
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

CREATE TYPE public.embedding_model AS ENUM (
  'universal_sentence_encoder_4',
  'txt_embed_ada_2'
);

CREATE TABLE IF NOT EXISTS public.embedding_use4 (
    analyzer_id bigint NOT NULL PRIMARY KEY,
    doc_id bigint,
    fragment_id bigint,
    scale public.fragment_type,
    embedding vector(512) NOT NULL,
    CONSTRAINT embedding_doc_id_key FOREIGN KEY (doc_id)
      REFERENCES public.document (id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT embedding_analyzer_key FOREIGN KEY (analyzer_id)
      references public.analyzer (id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT embedding_fragment_key FOREIGN KEY (fragment_id)
      references public.fragment (id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS public.embedding_ada2 (
    analyzer_id bigint NOT NULL PRIMARY KEY,
    doc_id bigint,
    fragment_id bigint,
    scale public.fragment_type,
    embedding vector(1536) NOT NULL,
    CONSTRAINT embedding_doc_id_key FOREIGN KEY (doc_id)
      REFERENCES public.document (id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT embedding_analyzer_key FOREIGN KEY (analyzer_id)
      references public.analyzer (id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT embedding_fragment_key FOREIGN KEY (fragment_id)
      references public.fragment (id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS embedding_use4_doc_id_idx on public.embedding_use4 (doc_id);
CREATE UNIQUE INDEX IF NOT EXISTS embedding_use4_fragment_doc_idx on public.embedding_use4 (fragment_id, doc_id);
CREATE INDEX IF NOT EXISTS embedding_use4_cosidx ON embedding_use4 USING ivfflat (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS embedding_ada2_doc_id_idx on public.embedding_ada2 (doc_id);
CREATE UNIQUE INDEX IF NOT EXISTS embedding_ada2_fragment_doc_idx on public.embedding_use4 (fragment_id, doc_id);
CREATE INDEX IF NOT EXISTS embedding_ada2_cosidx ON embedding_ada2 USING ivfflat (embedding vector_cosine_ops);

COMMIT;
