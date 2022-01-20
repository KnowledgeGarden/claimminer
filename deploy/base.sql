-- Deploy base
-- requires: admin_base
-- Copyright Society Library and Conversence 2022-2023

BEGIN;

CREATE SEQUENCE IF NOT EXISTS public.topic_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TYPE public.permission AS ENUM (
    'admin',
    'access',
    'add_document',
    'add_claim',
    'claim_score_query',
    'bigdata_query',
    'openai_query',
    'confirm_claim',
    'edit_prompts'
);

CREATE TYPE public.link_type AS ENUM (
    'generic',
    'key_point',
    'supported_by',
    'opposed_by',
    'implied',
    'implicit',
    'derived',
    'has_premise',
    'answers_question',
    'irrelevant',
    'relevant',
    'subcategory',
    'subclaim',
    'subquestion',
    'quote'
);

CREATE TYPE public.process_status AS ENUM (
    'pending',
    'ongoing',
    'complete',
    'error'
);


CREATE TYPE public.fragment_type AS ENUM (
  'document',
  'paragraph',
  'sentence',
  'phrase',
  'quote',
  'reified_arg_link',
  'standalone',
  'generated',
  'standalone_root',
  'standalone_category',
  'standalone_question',
  'standalone_claim',
  'standalone_argument'
);


CREATE TYPE public.id_score_type AS (
    id bigint, score float
);

COMMIT;
