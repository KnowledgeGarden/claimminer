-- Deploy analysis
-- requires: analyzer
-- requires: fragment
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

CREATE TABLE IF NOT EXISTS public.analysis (
    id bigint NOT NULL DEFAULT nextval('public.topic_id_seq'::regclass),
    analyzer_id bigint NOT NULL,
    theme_id bigint,
    params JSONB DEFAULT '{}',
    created timestamp without time zone NOT NULL DEFAULT now(),
    results JSONB NOT NULL,
    CONSTRAINT analysis_pkey PRIMARY KEY (id),
    CONSTRAINT analysis_analyzer_id_fkey FOREIGN KEY (analyzer_id)
      REFERENCES public.analyzer (id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT theme_id_fkey FOREIGN KEY (theme_id)
      REFERENCES public.fragment (id) ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS analysis_theme_idx ON analysis (theme_id);
CREATE UNIQUE INDEX IF NOT EXISTS analysis_unique_idx ON analysis (analyzer_id, theme_id, params);

CREATE TABLE IF NOT EXISTS public.analysis_context (
  analysis_id bigint NOT NULL,
  fragment_id bigint NOT NULL,

  CONSTRAINT analysis_context_pkey PRIMARY KEY (fragment_id, analysis_id),
  CONSTRAINT analysis_context_analysis_id_fkey FOREIGN KEY (analysis_id)
    REFERENCES public.analysis (id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT analysis_context_fragment_id_fkey FOREIGN KEY (fragment_id)
    REFERENCES public.fragment (id) ON DELETE CASCADE ON UPDATE CASCADE
);


CREATE INDEX IF NOT EXISTS analysis_context_analysis_idx ON analysis_context (analysis_id);

-- Circular dependency: fragment is created first, add constraint now if not exists.
DO
$$BEGIN
   IF (select count(oid) from pg_catalog.pg_constraint where conname='fragment_analysis_fkey') = 0 THEN
      ALTER TABLE public.fragment ADD CONSTRAINT fragment_analysis_fkey FOREIGN KEY (analysis_id)
        REFERENCES public.analysis (id) ON DELETE SET NULL ON UPDATE CASCADE;
   END IF;
END;$$;

COMMIT;
