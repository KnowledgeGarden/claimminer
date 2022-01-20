"""Utility functions for URI equivalence"""
# Copyright Society Library and Conversence 2022-2023

from urllib.parse import unquote
from collections import defaultdict
from typing import Optional, Dict, Tuple, List

from sqlalchemy import update, select, Integer
from sqlalchemy.sql.functions import count, func
from sqlalchemy.orm import aliased, joinedload

from .models import UriEquiv, Fragment, Document, Analysis, analysis_context_table
from . import Session, hashfs
from .uri import normalize


def is_archive(url):
    return url.startswith("https://web.archive.org/web/")

def url_from_archive_url(url):
      if is_archive(url):
          return unquote(url.split('/', 5)[-1])


async def add_urls(session, urls, equivalences: Optional[Dict[str, UriEquiv]]=None) -> Tuple[List[UriEquiv], List[UriEquiv]]:
    equivalences = equivalences or {}
    urls = {normalize(url) for url in urls if url.startswith("http")}  # basic sanity check
    existing = await session.execute(select(UriEquiv).filter(UriEquiv.uri.in_(urls)))
    existing = [uri for (uri,) in existing]
    urls = urls - {uri.uri for uri in existing}
    new_uris = [UriEquiv(
        uri=url, status='snapshot' if url in equivalences else 'canonical',
        canonical=equivalences.get(url, None)) for url in urls]
    session.add_all(new_uris)
    return new_uris, existing


async def add_documents(session, urls, collections=[]) -> Tuple[List[Document], List[Document]]:
    new_uris, existing = await add_urls(session, urls)
    docs = [
        Document(uri=uri, collections=collections)
        for uri in new_uris
    ]
    canonical = aliased(UriEquiv, name="canonical")
    existing_docs = await session.execute(
        select(Document).filter(Document.id.in_(
            select(Document.id).filter(Document.uri_id.in_([
                uri.id for uri in existing
                if uri.status in ('canonical', 'urn')])
            ).union(select(Document.id).join(canonical, Document.uri_id==canonical.id
            ).join(UriEquiv, UriEquiv.canonical_id==canonical.id
            ).filter(UriEquiv.id.in_(uri.id for uri in existing if uri.status != canonical)))
        )))
    existing_docs = [doc for (doc,) in existing_docs]
    session.add_all(docs)
    return docs, existing_docs


async def add_variant(session, new_uri, old_uri_eq, status='unknown'):
    # add a new variant to a UriEquiv group
    # Special cases:
    # Status=canonical: de-canonicalize the original
    if status == 'unknown':
        if old_uri_eq.status == 'urn':
            # Then we're the only URL
            status = 'canonical'
        else:
            # TODO: We have to chose one of them as canonical.
            # How? Shortest? But that disfavors permalinks. Punting in general
            status = 'alt'
    new_uri_eq = UriEquiv(uri=new_uri, status=status)
    session.add(new_uri_eq)
    if status == 'canonical':
        await merge(session, new_uri_eq, old_uri_eq)
    else:
        new_uri_eq.canonical = old_uri_eq


async def merge(session, canonical_uri_eq, old_uri_eq):
    # two URLs are now known to be identical, merge them.
    canonical_uri_eq.canonical_id = None
    if not canonical_uri_eq.id:
        session.add(canonical_uri_eq)
        await session.flush()
    if old_uri_eq.status == 'canonical':
        old_uri_eq.status = 'alt'
    old_uri_eq.canonical = canonical_uri_eq
    await session.execute(
            update(UriEquiv
                   ).where(UriEquiv.canonical_id==old_uri_eq.id
                   ).values(canonical_id=canonical_uri_eq.id))


async def doc_in_use(session, doc_id):
    # TODO: Make this a union query?
    r = await session.scalar(
        select(count(Analysis.id
              )).join(Fragment, Fragment.id == Analysis.theme_id).filter(Fragment.doc_id==doc_id))
    if r:
        return True
    r = await session.scalar(
        select(count(analysis_context_table.analysis_id
              )).join(Fragment, Fragment.id == analysis_context_table.fragment_id).filter(Fragment.doc_id==doc_id))
    if r:
        return True
    subq = select(func.jsonb_array_elements(Fragment.generation_data['sources']).label('id')).filter(Fragment.generation_data != None).cte("sources")
    r = await session.scalar(select(count(Fragment.id)).join(subq, subq.columns.id.cast(Integer) == Fragment.id).filter(Fragment.doc_id==doc_id))
    return r > 0


async def _migrate():
    # One-time migration function for duplicate documents.
    # Assumes that no duplicates are used for prompts
    # which was checked independently.
    async with Session() as session:
        docs = await session.execute(
            select(Document
                   ).order_by(Document.file_identity == None,
                              Document.retrieved.desc(), Document.id.desc()))
        docs = [doc for (doc,) in docs]
        by_norm = defaultdict(list)
        for doc in docs:
            by_norm[normalize(doc.url)].append(doc)
        for docs in by_norm.values():
            latest = docs[0]
            for doc in docs[1:]:
                await session.delete(doc.uri)
                await session.delete(doc)
                if doc.file_identity and doc.file_identity != latest.file_identity:
                    hashfs.delete(doc.file_identity)
                if doc.text_identity and doc.text_identity != latest.file_identity:
                    hashfs.delete(doc.text_identity)
        await session.flush()

        for uri, docs in by_norm.items():
            latest = docs[0]
            latest.uri.uri = uri
            latest.uri.status = 'canonical'
        await session.commit()


# TODO: Case of an attempted merge because of DOI collision but the text content is clearly different.
# In that case we want to record the document with a ALT-doi URI to be reviewed by the operator. Has to be stored in document.
# Maybe is_archive could become an enum.
