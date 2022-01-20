"""
Copyright Society Library and Conversence 2022-2023
"""
import argparse
import asyncio

from sqlalchemy.future import select
from sqlalchemy import cast, ARRAY, Float
from sqlalchemy.orm import aliased

from .. import Session, hashfs, get_analyzer_id
from ..embed import tf_embed
from ..models import Document, Fragment, Collection, embed_models, BASE_EMBED_MODEL
from . import logger

version = 1

async def do_embeds(texts, analyzer_id, scale, doc_id, fragment_id=None, model=BASE_EMBED_MODEL):
        base_embed = await tf_embed(texts, model)
        Embedding = embed_models[model]
        return Embedding(
            doc_id=doc_id, analyzer_id=analyzer_id, fragment_id=fragment_id, scale=scale,
            embedding=cast(base_embed, ARRAY(Float)))


async def do_embed_doc(doc_ids, analyzer_id, model=BASE_EMBED_MODEL):
    async with Session() as session:
        Embedding = embed_models[model]
        r = await session.execute(
            select(Document
            ).outerjoin(Embedding, (Embedding.doc_id==Document.id) & (Embedding.fragment_id==None)
            ).filter(Document.id.in_(doc_ids), Document.text_identity != None, Embedding.doc_id == None))
        r = r.all()
        if not r:
            logger.error("Missing document %s", doc_ids)
            return False
        docs = [doc for (doc,) in r]
        texts = []
        for doc in docs:
            finfo = hashfs.get(doc.text_identity)
            with open(finfo.abspath, 'r') as f:
                text = f.read()
            texts.append(text)
        embeddings = await tf_embed(texts, model)

        for (doc, embedding) in zip(docs, embeddings):
            session.add(Embedding(
                doc_id=doc.id, analyzer_id=analyzer_id, scale="document",
                embedding=cast(embedding, ARRAY(Float))))
        await session.commit()


async def do_embed_fragment(fragment_ids, analyzer_id, model=None, max_size=20000):
    model = model or BASE_EMBED_MODEL
    async with Session() as session:
        Embedding = embed_models[model]
        r = await session.execute(
            select(Fragment, Embedding.fragment_id
            ).outerjoin(Embedding, (Embedding.fragment_id==Fragment.id)
            ).filter(Fragment.id.in_(fragment_ids)))
        fragments = [fragment for (fragment, embedding_fid) in r if not embedding_fid]
        excluded = []
        if not fragments:
            return [], excluded
        if max_size:
            use = []
            cumulative_size = 0

            for f in fragments:
                l = len(f.text)
                if l > max_size:
                    logger.warning(f"fragment %d has length %d > %d", f.id, l, max_size)
                    excluded.append(f.id)
                    continue
                cumulative_size += l
                if cumulative_size > max_size:
                    break
                use.append(f)
            fragments = use
        ids = [f.id for f in fragments]
        if missing := set(fragment_ids) - set(ids):
            logger.warning("Missing fragments: %s", missing)
        if fragments:
            embeddings = await tf_embed([f.text for f in fragments], model)
        else:
            embeddings = []
        for (fragment, embedding) in zip(fragments, embeddings):
            session.add(Embedding(
                doc_id=fragment.doc_id, analyzer_id=analyzer_id, scale=fragment.scale,
                fragment_id=fragment.id, embedding=cast(embedding, ARRAY(Float))))
        await session.commit()
    return ids, excluded


async def batch_embed(
        documents=True, fragments=True, batch_size=10, model=BASE_EMBED_MODEL,
        collection=None, pause_after=None, pause_length=60, max_size=20000):
    analyzer_id = await get_analyzer_id("embed", version)
    Embedding = embed_models[model]
    num_requests = 0
    while documents:
        async with Session() as session:
            q = select(Document.id
                ).outerjoin(
                    Embedding,
                    (Embedding.doc_id == Document.id) &
                    (Embedding.fragment_id == None)
                ).filter(Embedding.fragment_id == None, Document.text_identity != None
                ).limit(batch_size)
            if collection:
                q = q.join(Collection, Document.collections).filter(Collection.name == collection)
            r = await session.execute(q)
            doc_ids = r.all()
        if not doc_ids:
            break
        doc_ids = [id for (id,) in doc_ids]
        await do_embed_doc(doc_ids, analyzer_id, model)
        logger.debug("documents: %s", doc_ids)
        num_requests += 1
        if (pause_after is not None) and (num_requests > pause_after):
            await asyncio.sleep(pause_length)
            num_requests = 0
    excluded = set()
    while fragments:
        async with Session() as session:
            q = select(Fragment.id
                ).outerjoin(
                    Embedding,
                    (Embedding.fragment_id == Fragment.id)
                ).filter(Embedding.fragment_id == None, Fragment.id.not_in(excluded), Fragment.text != '').limit(batch_size)
            if collection:
                doc_collection = aliased(Collection, name="doc_collection")
                claim_collection = aliased(Collection, name="claim_collection")
                q = q.outerjoin(Document, Document.id == Fragment.doc_id
                    ).outerjoin(doc_collection, Document.collections
                    ).outerjoin(claim_collection, Fragment.collections
                    ).filter((claim_collection.name == collection) | (doc_collection.name == collection))
            r = await session.execute(q)
            frag_ids = r.all()
        if not frag_ids:
            break
        frag_ids = [id for (id,) in frag_ids]
        processed, new_excluded = await do_embed_fragment(frag_ids, analyzer_id, model, max_size)
        excluded.update(new_excluded)
        logger.info("fragments: %s", processed)
        num_requests += 1
        if (pause_after is not None) and (num_requests > pause_after):
            await asyncio.sleep(pause_length)
            num_requests = 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fragments", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--batch_size", type=int, default=10)
    parser.add_argument("--pause_after", type=int, default=None)
    parser.add_argument("--pause_length", type=int, default=60)
    parser.add_argument("--max_size", type=int, default=0)
    parser.add_argument("--collection")
    parser.add_argument("--model", choices=list(embed_models.keys()), default=BASE_EMBED_MODEL)
    args = parser.parse_args()
    asyncio.run(batch_embed(**vars(args)))
