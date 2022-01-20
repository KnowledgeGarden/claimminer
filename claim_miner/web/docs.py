"""
Copyright Society Library and Conversence 2022-2023
"""
from io import BytesIO, TextIOWrapper
from datetime import datetime
from csv import reader
from collections import defaultdict
from itertools import groupby, chain
from pathlib import Path
import re

import simplejson as json
from quart import request, render_template, send_file, jsonify
from quart_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.exceptions import Unauthorized, BadRequest, NotFound
from sqlalchemy import cast, Float, Boolean
from sqlalchemy.sql.functions import count, max as fmax, min as fmin, coalesce
from sqlalchemy.sql.expression import func
from sqlalchemy.orm import aliased, subqueryload
import isodate

from .. import Session, select, hashfs, as_bool
from ..models import Analysis, Document, Fragment, ClaimLink, Collection, UriEquiv, embed_models
from ..app import app, current_user, get_channel, logger
from ..auth import may_require_collection_permission, doc_collection_constraints, check_doc_access, requires_collection_permission, set_user
from ..nlp import as_prompts
from ..uri import normalize
from .. import uri_equivalence
from . import render_with_spans, get_collection, get_base_template_vars, get_collections_and_scope

mimetypes = {
    "html": "text/html",
    "pdf": "application/pdf",
    "txt": "text/plain",
}

@app.route("/doc")
@app.route("/c/<collection>/doc")
@may_require_collection_permission('access')
async def list_docs(collection=None):
    offset = request.args.get("start", type=int, default=0)
    limit = request.args.get("limit", type=int, default=30)
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        sq = select(Document.id).order_by(Document.id).offset(offset).limit(limit)
        if collection or not await current_user.can('access'):
            sq = await doc_collection_constraints(sq, collection)
        # subquery load instead of grouping by uri
        q = select(Document, count(Fragment.id)
            ).outerjoin(Fragment, Fragment.doc_id==Document.id
            ).group_by(Document.id).options(subqueryload(Document.uri)).filter(Document.id.in_(sq))
        r = await session.execute(q)
        data = r.fetchall()
    docs = [x[0] for x in data]
    num_paras = {x[0].id: x[1:] for x in data}
    previous = max(offset - limit, 0) if offset > 0 else ""
    next = (offset + limit) if len(docs) == limit else ""
    end = offset + len(docs)
    return await render_template("list_docs.html", docs=docs, offset=offset, end=end, prev=previous, next=next, limit=limit, num_paras=num_paras, **base_vars)


@app.route("/doc/<int:doc_id>/raw")
@app.route("/c/<collection>/doc/<int:doc_id>/raw")
@requires_collection_permission('access')
async def get_raw_doc(doc_id, collection=None):
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        r = await session.execute(select(Document.file_identity, Document.mimetype).filter_by(id=doc_id))
        if r is None:
            raise NotFound()
        (file_identity, mimetype, public_contents) = r.first()
        if not (public_contents or await current_user.can('admin')):
            raise Unauthorized("Copyrighted content")
        await check_doc_access(doc_id, collection)
        file_info = hashfs.get(file_identity)
        extension = mimetype.split("/")[1]
        return await send_file(file_info.abspath, mimetype, True, f"{doc_id}.{extension}")


@app.route("/api/doc")
@app.route("/api/c/<collection>/doc")
@jwt_required
async def get_doc_id_list_api(collection=None):
    current_user = await set_user(get_jwt_identity())
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        if not await collection.user_can(current_user, 'access'):
            raise Unauthorized()
        query = select(Document.id, UriEquiv.uri.label('url')).join(UriEquiv, Document.uri)
        if collection:
            query = query.join(Collection, Document.collections).filter(Collection.name==collection.name)
        r = await session.execute(query)
        return jsonify([row._asdict() for row in r])


@app.route("/api/doc/<int:doc_id>/raw")
@app.route("/api/c/<collection>/doc/<int:doc_id>/raw")
@jwt_required
async def get_raw_doc_api(doc_id, collection=None):
    current_user = await set_user(get_jwt_identity())
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        r = await session.execute(select(Document.file_identity, Document.mimetype).filter_by(id=doc_id))
        if r is None:
            raise NotFound()
        (file_identity, mimetype, public_contents) = r.first()
        if not (public_contents or await current_user.can('admin')):
            raise Unauthorized("Copyrighted content")
        await check_doc_access(doc_id, collection)
        file_info = hashfs.get(file_identity)
        extension = mimetype.split("/")[1]
        return await send_file(file_info.abspath, mimetype, True, f"{doc_id}.{extension}")



@app.route("/doc/<int:doc_id>")
@app.route("/c/<collection>/doc/<int:doc_id>")
@may_require_collection_permission('access')
async def get_doc_info(doc_id: int, collection=None):
    oorder = request.args.get("order", type=str, default="para")
    inverted = oorder[0] == '-'
    if inverted:
        order = oorder[1:]
    else:
        order = oorder
    # logger.debug(order, inverted)
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        await check_doc_access(doc_id, collection)
        r = await session.execute(
            select(Document).filter(Document.id==doc_id).limit(1))
        # TODO: Add the claims
        if r is None:
            raise NotFound()
        (doc,) = r.first()
        public_contents = doc.public_contents or await current_user.can('admin')
        # Count available embeddings
        num_embeddings = dict()
        for model, Embedding in embed_models.items():
            frag_embedding = aliased(Embedding)
            doc_embedding = aliased(Embedding)
            q = select(Document.id, count(frag_embedding.fragment_id)+count(doc_embedding.doc_id.distinct())
                ).outerjoin(Fragment, Fragment.doc_id==Document.id
                ).outerjoin(frag_embedding, frag_embedding.fragment_id==Fragment.id
                ).outerjoin(doc_embedding, (doc_embedding.doc_id==Document.id) & (doc_embedding.fragment_id == None)
                ).group_by(Document.id).filter(Document.id==doc_id)
            r = await session.execute(q)
            (id, emb_count) = r.first()
            num_embeddings[model] = emb_count
        has_embedding = bool(sum(num_embeddings.values()))

        # Get the paragraphs themselves
        source = aliased(Fragment)
        key_point = aliased(Fragment)
        key_point_doc = aliased(Document)
        para = aliased(Fragment)
        fragment_query = select(Fragment).filter(Fragment.doc_id==doc_id, Fragment.scale=="paragraph")
        if order == "para":
            fragment_query = fragment_query.order_by(Fragment.position)
            if not public_contents:
                fragment_query = fragment_query.outerjoin(
                    Analysis, Fragment.id==Analysis.theme_id
                    ).outerjoin(
                    ClaimLink, (ClaimLink.target==Fragment.id) | (ClaimLink.source==Fragment.id)
                    ).filter((ClaimLink.source != None) | (Analysis.theme_id != None)).distinct()
        else:
            order_col = cast(Analysis.results[order], Float)
            fragment_query = fragment_query.join(Analysis, Fragment.id==Analysis.theme_id
                ).filter(coalesce(cast(Analysis.params[order], Boolean), False)
                ).group_by(Fragment.id)
            if inverted:
                fragment_query = fragment_query.order_by(fmin(order_col))
            else:
                fragment_query = fragment_query.order_by(fmax(order_col).desc())
        r = await session.execute(fragment_query)
        paras = [para for (para,) in r]
        num_fragments = len(paras)

        # Get claim quality analysis
        analysis_query = select(Analysis
            ).join(source, source.id == Analysis.theme_id
            ).filter(source.doc_id==doc_id)
        if (order != "para"):
            analysis_query = analysis_query.filter(coalesce(cast(Analysis.params[order], Boolean), False))
        r = await session.execute(analysis_query)
        analyses = defaultdict(list)
        theme_ids = set()
        for (analysis,) in r:
            analyses[analysis.theme_id].append(analysis)
            theme = analysis.params.get("theme")
            if theme:
                theme_ids.add(int(theme))
        r = await session.execute(
            select(Fragment).filter(Fragment.id.in_(theme_ids)))
        themes = {t.id: t for (t,) in r}

        # Get boundaries
        boundaries_query = select(Fragment
            ).outerjoin(ClaimLink, (ClaimLink.source == Fragment.id) | (ClaimLink.target == Fragment.id)
            ).filter(Fragment.doc_id==doc_id, Fragment.scale=="generated", ClaimLink.source == None)
        if order != "para":
            boundaries_query = boundaries_query.join(
                Analysis, Fragment.part_of==Analysis.theme_id
                ).filter(coalesce(cast(Analysis.params[order], Boolean), False))
        r = await session.execute(boundaries_query.order_by(Fragment.part_of))
        boundaries = {f.part_of: [(None, f)] for (f,) in r}
        ids = set(boundaries.keys())
        spans = {id: list(boundaries.get(id, ())) for id in ids}
        renderings = {p.id: render_with_spans(p.text, spans.get(p.id, [])) for p in paras}

        return await render_template(
            "doc_info.html", doc=doc, has_embedding=has_embedding, num_fragments=num_fragments, order=oorder,
            num_frag_embeddings=num_embeddings, paras=paras, analyses=analyses, themes=themes, public_contents=public_contents,
            renderings=renderings, **base_vars)


@app.route("/doc/<int:doc_id>/text")
@app.route("/c/<collection>/doc/<int:doc_id>/text")
@may_require_collection_permission('access')
async def get_text_doc(doc_id, collection=None):
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        r = await session.execute(select(Document.text_identity, Document.mimetype, Document.public_contents).filter_by(id=doc_id))
        if r is None:
            raise NotFound()
        (text_identity, mimetype, public_contents) = r.first()
        if not (public_contents or await current_user.can('admin')):
            raise Unauthorized("Copyrighted content")
        await check_doc_access(doc_id, collection)
        file_info = hashfs.get(text_identity)
        return await send_file(file_info.abspath, mimetype, True, f"{doc_id}.txt")


@app.route("/api/doc/<int:doc_id>/text")
@app.route("/api/c/<collection>/doc/<int:doc_id>/text")
@jwt_required
async def get_text_doc_api(doc_id, collection=None):
    current_user = await set_user(get_jwt_identity())
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        r = await session.execute(select(Document.text_identity, Document.mimetype, Document.public_contents).filter_by(id=doc_id))
        if r is None:
            raise NotFound()
        (file_identity, mimetype, public_contents) = r.first()
        if not (public_contents or await current_user.can('admin')):
            raise Unauthorized("Copyrighted content")
        await check_doc_access(doc_id, collection)
        file_info = hashfs.get(file_identity)
        return await send_file(file_info.abspath, mimetype, True, f"{doc_id}.txt")


def compose_url_jsonl(data, spec):
    if '|' in spec:
        specs = spec.split('|')
        for spec in specs:
            if url := compose_url_jsonl(data, spec):
                return url
    part_specs = spec.split(',')
    parts = []
    for spec in part_specs:
        spec = spec.strip()
        if spec.startswith("'"):
            parts.append(spec.strip("'"))
            continue
        slugify = spec.startswith("#")
        spec = spec.strip("#")
        prefix = None
        if '-' in spec:
            spec, prefix = spec.split('-')
            prefix = int(prefix)
        part = data.get(spec, None)
        if slugify:
            part = part.encode('ascii', 'replace').decode('ascii')
            part = re.sub(r'\W+', "_", part, 0, re.ASCII)
            part = part.strip('_')
        if part == "n/a":
            part = None
        if not part:
            return None
        if prefix:
            part = part[prefix:]
        parts.append(part)
    return "/".join(parts)


def maybe_flatten(str_or_list):
    if isinstance(str_or_list, list):
        return "\n\n".join(str_or_list)
    return str_or_list


def get_text_jsonl(data, text_fields="text", text_process=None):
    text_fields = text_fields.split(",")
    text = "\n\n".join(maybe_flatten(data.get(field.strip(), "")) for field in text_fields)
    if text_process:
        for pat, repl in text_process:
            text = re.sub(pat, repl, text)
    return text


@app.route("/doc/upload", methods=['GET', 'POST'])
@app.route("/c/<collection>/doc/upload", methods=['GET', 'POST'])
@may_require_collection_permission('add_document')
async def upload_docs(collection=None):
    error = ""
    success = ""
    warning = ""
    new_ids = []
    if request.method == 'POST':
        await request.get_data()
        form = await request.form
        collections = []
        try:
            async with Session() as session:
                base_vars = await get_base_template_vars(current_user, collection, session)
                if collection_ob := base_vars['collection']:
                    collections.append(collection_ob)
            if form.get("upload_type") == "single":
                url = form.get("url").strip()
                doc_given = False
                if not url:
                    raise BadRequest("URL is required")
                async with Session() as session:
                    url = normalize(url)
                    r = await session.scalar(select(UriEquiv.id).filter_by(uri=url).limit(1))
                    # TODO: UX to load a new snapshot of an existing document
                    if r is not None:
                        raise BadRequest("Document with this URL already exists")
                    if request._files.get("file"):
                        files = await request.files
                        fs = files.get("file")
                        extension = fs.filename.lower().split(".")[-1]
                        mimetype = mimetypes.get(extension)
                        if not mimetype:
                            warning = f"unknown file extension: {extension}"
                            logger.warn(warning)
                        file_identity = hashfs.put(BytesIO(fs.stream.read()))
                        text_identity_id = file_identity.id if extension == "txt" else None
                        if Path(file_identity.abspath).stat.st_size > 1000:
                            # Avoid identifying small stubs
                            r = await session.execute(select(Document.uri).filter_by(file_identity=file_identity.id).limit(1))
                            if r := r.first():
                                (uri_equiv,) = r
                                await uri_equivalence.add_variant(session, url, uri_equiv)
                                await session.commit()
                                raise BadRequest(f"Document with this file already exists at URL {uri_equiv.uri}")
                            uri = UriEquiv(uri=url)
                        doc = Document(
                            uri=uri, file_identity=file_identity.id, text_identity=text_identity_id,
                            mimetype=mimetype, added_by=current_user.auth_id, return_code=200, collections=collections,
                            retrieved=datetime.utcnow())
                        doc_given = True
                    else:
                        uri = UriEquiv(uri=url)  # tentatively canonical?
                        doc = Document(uri=uri, added_by=current_user.auth_id)
                    session.add(doc)
                    await session.commit()
                    success = "Document added"
                new_ids = [doc.id]
                if doc_given:
                    if (mimetype == "application/pdf"):
                        await get_channel("process_pdf").send_soon(key=str(doc.id), value=doc.id)
                    elif (mimetype == "text/html"):
                        await get_channel("process_html").send_soon(key=str(doc.id), value=doc.id)
                    elif (mimetype in ("text/plain", "text/markdown")):  # markdown
                        await get_channel("process_text").send_soon(key=str(doc.id), value=doc.id)
                else:
                    await get_channel("download").send_soon(key=str(doc.id), value=doc.id)
            elif form.get("upload_type") == "csv":
                files = await request.files
                fs = files.get("file")
                r = reader(TextIOWrapper(fs, "utf-8"))
                if as_bool(form.get("skip")):
                    next(r)
                column = int(form.get("column")) - 1
                urls = [row[column].strip() for row in r]
                urls = [normalize(url) for url in urls if url.startswith("http")]  # basic sanity check
                async with Session() as session:
                    uris, existing = uri_equivalence.add_urls(session, urls)
                    if existing:
                        logger.warn(f"Already existing URLs: {[uri.uri for uri in existing]}")
                    docs = [
                        Document(
                            uri=uri,
                            added_by=current_user.auth_id,
                            collections=collections,
                        )
                        for uri in uris
                    ]
                    session.add_all(docs)
                    await session.commit()
                for doc in docs:
                    await get_channel("download").send_soon(key=str(doc.id), value=doc.id)
                success = f"{len(docs)} documents added"
            elif form.get("upload_type") == "jsonl":
                files = await request.files
                fs = files.get("file")
                r = TextIOWrapper(fs, "utf-8")
                url_spec = form.get("url_spec")
                text_fields = form.get("text_fields", "text")
                use_title = as_bool(form.get("use_title", "true"))
                use_published = as_bool(form.get("use_published", "false"))
                extra_newlines = as_bool(form.get("extra_newlines", "false"))
                published_field = "date_published"
                urls = set()
                docs = []
                async with Session() as session:
                    for line in r:
                        data = json.loads(line)
                        url = normalize(compose_url_jsonl(data, url_spec))
                        if url in urls:
                            continue
                        urls.add(url)
                        text = get_text_jsonl(data, text_fields, [[r" \n\n", " "]] if extra_newlines else None)
                        title = data.get("title") if use_title else None
                        published = None
                        if use_published:
                            published = data[published_field]
                            if published == 'n/a':
                                published = None
                            if published:
                                if ' ' in published:
                                    published = 'T'.join(published.split())
                                if 'T' in published:
                                    published = isodate.parse_datetime(published)
                                else:
                                    published = isodate.parse_date(published)
                        if isinstance(text, list):
                            text = "\n".join(text)
                        existing = await session.scalar(select(count(UriEquiv.uri)).filter_by(uri=url))
                        if existing:
                            continue
                        json_as_file = hashfs.put(BytesIO(line.encode('utf-8')))
                        txt_as_file = hashfs.put(BytesIO(text.encode('utf-8')))
                        docs.append(Document(
                            uri=UriEquiv(uri=url), added_by=current_user.auth_id, title=title, collections=collections,
                            file_identity=json_as_file.id, file_size=Path(json_as_file.abspath).stat().st_size,
                            text_identity=txt_as_file.id, text_size=Path(txt_as_file.abspath).stat().st_size,
                            mimetype='text/plain', created=published, return_code=200))
                    for doc in docs:
                        session.add(doc)
                    await session.commit()
                for doc in docs:
                    await get_channel("process_text").send_soon(key=str(doc.id), value=doc.id)
                success = f"{len(docs)} documents added"
        except Exception as e:
            logger.exception("")
            error = str(e)
    else:
        base_vars = await get_base_template_vars(current_user, collection)

    return await render_template("upload_docs.html", error=error, success=success, new_ids=new_ids, **base_vars)


@app.route("/api/doc", methods=["POST"])
@app.route("/api/c/<collection>/doc", methods=["POST"])
@jwt_required
async def add_doc_json(collection=None):
    current_user = await set_user(get_jwt_identity())
    json = await request.json
    if not json:
        raise BadRequest("Please post JSON")
    text = json['text']
    async with Session() as session:
        collections, collection = await get_collections_and_scope(json.get('collection', collection), session, current_user.auth_id)
        can_add = await collection.user_can(current_user, 'add_document')
        if not can_add:
            raise Unauthorized()

        url = json.get("url").strip()
        url = normalize(url)
        r = await session.scalar(select(UriEquiv.id).filter_by(uri=url).limit(1))
        # TODO: UX to load a new snapshot of an existing document
        if r is not None:
            raise BadRequest("Document with this URL already exists")
        uri = UriEquiv(uri=url)  # tentatively canonical?
        doc = Document(uri=uri, added_by=current_user.auth_id, collections=collections)
        session.add(doc)
        await session.commit()
    await get_channel("download").send_soon(key=str(doc.id), value=doc.id)
    return dict(id=doc.id)


@app.route("/doc/<int:doc_id>/completion_prompts")
@app.route("/c/<collection>/doc/<int:doc_id>/completion_prompts")
@may_require_collection_permission('access')
async def as_completions(doc_id, collection=None):
    await check_doc_access(doc_id, collection)
    io = BytesIO()
    tf = TextIOWrapper(io, encoding='utf-8')
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        fragment_query = select(Fragment.text).filter(Fragment.doc_id==doc_id, Fragment.scale=="paragraph"
            ).order_by(Fragment.position)
        r = await session.execute(fragment_query)
        for (para,) in r:
            for (prompt, completion) in as_prompts(para):
                json.dump(dict(prompt=prompt, completion=completion), tf, False)
                tf.write("\n")
    tf.flush()
    io.seek(0)
    io = BytesIO(io.read())     # WHY IS THIS NECESSARY?
    return await send_file(io, "application/json-l", True, f"prompts_{doc_id}.jsonl")
