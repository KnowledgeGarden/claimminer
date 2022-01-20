"""
Copyright Society Library and Conversence 2022-2023
"""
from datetime import datetime
from io import TextIOWrapper, BytesIO
from csv import reader, writer
from itertools import chain

import simplejson as json
from quart import request, render_template, redirect, jsonify, Response
from quart_jwt_extended import jwt_required, get_jwt_identity
from quart.wrappers import Response
from quart.wrappers.response import IOBody
from werkzeug.exceptions import NotFound, Unauthorized, BadRequest
from sqlalchemy import Integer, Float, cast
from sqlalchemy.sql import desc, literal_column
from sqlalchemy.sql.functions import count
from sqlalchemy.sql.expression import func
from sqlalchemy.orm import aliased, joinedload, subqueryload
from sqlalchemy.exc import IntegrityError

from .. import Session, select, as_bool, config
from ..models import (
    Document, Fragment, en_regconfig, UriEquiv, Analysis, visible_standalone_type_names, link_type_names,
    Analyzer, ClaimLink, Collection, embed_models, visible_standalone_types, standalone_type_names, claim_neighbourhood
)
from ..app import app, login_required, current_user, get_channel, logger
from ..auth import may_require_collection_permission, fragment_collection_constraints, set_user
from . import get_collection, update_fragment_selection, get_base_template_vars, schedule_fragment_embeds, get_collections_and_scope
from ..debatemap_client import export_node, debatemap_query, path_query


mimetypes = {
    "html": "text/html",
    "pdf": "application/pdf",
    "txt": "text/plain",
}

@app.route("/claim", methods=["POST"])
@app.route("/c/<collection>/claim", methods=["POST"])
@may_require_collection_permission('add_claim')
async def add_claim(collection=None):
    form = await request.form
    text = form.get("text")
    node_type = form.get("node_type")
    if not text:
        return redirect("/claim")
    async with Session() as session:
        collection = await get_collection(collection, session, current_user.auth_id)
        claim = Fragment(text=form.get("text"), scale=node_type, language="en", char_position=0, position=0, created_by=current_user.auth_id)
        if collection:
            claim.collections = [collection]
        session.add(claim)
        await session.commit()
    await schedule_fragment_embeds([claim.id], [collection])
    return redirect(f"{collection.path}/claim/{claim.id}")


@app.route("/api/claim/<int:id>")
@app.route("/api/c/<collection>/claim/<int:id>")
@jwt_required
async def get_claim_json(id, collection=None):
    current_user = await set_user(get_jwt_identity())
    async with Session() as session:
        collections, collection = await get_collections_and_scope(collection, session, current_user.auth_id)
        can_read = await collection.user_can(current_user, 'access')
        if not can_read:
            raise Unauthorized()
        query = select(Fragment).filter_by(id=id).options(joinedload(Fragment.document).joinedload(Document.uri))
        r = await session.execute(query)
        # TODO: verify collection access
        try:
            (claim,) = r.one()
        except:
            raise NotFound()
    if claim.scale == "paragraph":
        return dict(id=claim.id, text=claim.text, doc_id=claim.doc_id, title=claim.document.title, url=claim.document.url)
    else:
        return dict(id=claim.id, text=claim.text, node_type=claim.scale)


@app.route("/api/claim", methods=["POST"])
@app.route("/api/c/<collection>/claim", methods=["POST"])
@jwt_required
async def add_claim_json(collection=None):
    current_user = await set_user(get_jwt_identity())
    json = await request.json
    if not json:
        raise BadRequest("Please post JSON")
    text = json['text']
    node_type = json.get('node_type', 'standalone_claim')
    if node_type not in visible_standalone_type_names:
        raise BadRequest(f"Invalid node_type : Must be one of {', '.join(visible_standalone_type_names.keys())}")
    async with Session() as session:
        collections, collection = await get_collections_and_scope(json.get('collection', collection), session, current_user.auth_id)
        can_add = await collection.user_can(current_user, 'add_claim')
        if not can_add:
            raise Unauthorized()
        confirm = await collection.user_can(current_user, 'confirm_claim')
        claim = Fragment(text=text, scale=node_type, language=json.get('lang', 'en'), char_position=0, position=0,
                        created_by=current_user.auth_id, confirmed=confirm, collections=collections)
        session.add(claim)
        try:
            await session.commit()
        except IntegrityError as e:
            await session.rollback()
            raise BadRequest("Duplicate text")
    await schedule_fragment_embeds([claim.id], [collection])
    base_url = request.root_url
    location = f"{base_url}api{collection.path}/claim/{claim.id}"
    return Response(f"Created: {claim.id}", status=201, headers=dict(location=location))


@app.route("/api/claim/<int:id>/links", methods=["POST"])
@app.route("/api/c/<collection>/claim/<int:id>/links", methods=["POST"])
@jwt_required
async def add_claim_link_json(id, collection=None):
    current_user = await set_user(get_jwt_identity())
    json = await request.json
    if not json:
        raise BadRequest("Please post JSON")
    link_type = json.get('link_type', 'quote')
    if link_type not in visible_standalone_type_names and link_type != 'quote':
        raise BadRequest(f"Invalid link_type : Must be one of {', '.join(chain(link_type_names.keys(), ('quote',)))}")
    target = json.get("target")
    if not isinstance(target, int):
        raise BadRequest("JSON must contain a target, which is also a claim id.")
    if target == id:
        raise BadRequest("Source and target must be different")
    async with Session() as session:
        collections, collection = await get_collections_and_scope(json.get('collection', collection), session, current_user.auth_id)
        can_add = await collection.user_can(current_user, 'add_claim')
        if not can_add:
            raise Unauthorized()
        claims_r = await session.execute(select(Fragment.id).filter(Fragment.id.in_((target, id))))
        claims_r = set(x for (x,) in claims_r)
        if id not in claims_r:
            raise NotFound(f"Missing claim {id}")
        if target not in claims_r:
            raise NotFound(f"Missing target claim {target}")
        # confirm = await collection.user_can(current_user, 'confirm_claim')
        # TODO: proposed claim_links!
        # TODO: Validation that node types are coherent with link_type
        link = ClaimLink(source=id, target=target, link_type=link_type, created_by=current_user.auth_id)
        session.add(link)
        await session.commit()
    return Response("Created", status=201)



@app.route("/claim")
@app.route("/c/<collection>/claim")
@may_require_collection_permission('access')
async def list_claims(collection=None):
    offset = request.args.get("start", type=int, default=0)
    limit = request.args.get("limit", type=int, default=30)
    search_text = request.args.get("search_text", type=str, default=None)
    filetype = request.args.get("type", None)
    as_json = filetype=='json' or (request.accept_mimetypes.quality("application/json") > request.accept_mimetypes.quality("text/html"))
    as_csv = filetype=='csv' or (request.accept_mimetypes.quality("text/csv") > request.accept_mimetypes.quality("text/html"))
    as_data = as_json or as_csv
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        if as_data:
            query = select(Fragment).filter(Fragment.is_visible_claim)
        else:
            query = select(Fragment, count(Analysis.id).label("num_analysis")).filter(Fragment.is_visible_claim
                ).outerjoin(Analysis, cast(Analysis.params['theme'], Integer)==Fragment.id
                ).group_by(Fragment.id).order_by(Fragment.text)
        if collection or not await current_user.can('access'):
            query = await fragment_collection_constraints(query, collection)
        if search_text is not None:
            tsquery = func.plainto_tsquery(en_regconfig, search_text).label('tsquery')
            vtext = func.to_tsvector(Fragment.text)
            tsrank = func.ts_rank_cd(vtext, tsquery).label('rank')
            query = query.filter(Fragment.ptmatch()(tsquery)).rank(desc(tsrank))
        else:
            query = query.order_by(Fragment.id)
        r = await session.execute(query.offset(offset).limit(limit))
        claims = r.fetchall()
        claim_indices = []
        if offset == 0 and not as_data:
            query = select(Fragment).filter_by(scale="standalone_root").order_by(Fragment.text)
            if collection or not await current_user.can('access'):
                query = await fragment_collection_constraints(query, collection)
            r = await session.execute(query)
            claim_indices = [c for (c,) in r.fetchall()]
    if as_json:
        title = f"claims_{collection.name}.json" if collection else "claims.json"
        r = jsonify([dict(id=claim.id, text=claim.text, type=claim.scale) for (claim,) in claims])
        r.headers.add("content-disposition", f"attachment;filename={title}")
        return r
    elif as_csv:
        title = f"claims_{collection.name}.csv" if collection else "claims.csv"
        output = BytesIO()
        output_utf8 = TextIOWrapper(output, encoding="utf-8")
        csv = writer(output_utf8, dialect='excel', delimiter=';')
        csv.writerow(["id", "type", "text"])
        for (claim,) in claims:
            csv.writerow([claim.id, claim.scale, claim.text])
        output_utf8.detach()
        output.seek(0)
        return Response(IOBody(output), mimetype="text/csv", headers={"content-disposition": f"attachment;filename={title}"})
    previous = max(offset - limit, 0) if offset > 0 else ""
    next_ = (offset + limit) if len(claims) == limit else ""
    end = offset + len(claims)
    return await render_template(
        "list_claims.html", claims=claims, claim_indices=claim_indices,
        offset=offset, prev=previous, next=next_, limit=limit, end=end,
        search_text=search_text, **base_vars)


@app.route("/claim/<int:id>")
@app.route("/c/<collection>/claim/<int:id>")
@may_require_collection_permission('access')
async def claim_info(id, collection=None):
    offset = int(request.args.get('start') or 0)
    limit = int(request.args.get('limit') or 10)
    async with Session() as session:
        prompt_analyzers = await session.execute(select(Analyzer.id, Analyzer.nickname).order_by(Analyzer.nickname).filter_by(name="simple_prompt_analyzer"))
        prompt_analyzers = prompt_analyzers.all()
        base_vars = await get_base_template_vars(current_user, collection, session)
        r = await session.execute(
            select(Fragment
            ).filter(Fragment.id==id, Fragment.is_claim
            ).options(
                joinedload(Fragment.from_analysis).joinedload(Analysis.analyzer),
                subqueryload(Fragment.outgoing_links).joinedload(ClaimLink.target_fragment),
                subqueryload(Fragment.incoming_links).joinedload(ClaimLink.source_fragment),
            ).limit(1))
        claim = r.first()
        if not claim:
            raise NotFound()
        claim = claim[0]
        claim_nghd = await claim_neighbourhood(id, session)
        source = aliased(Fragment, name="source")
        source_doc = aliased(Document, name="source_doc")
        key_point = aliased(Fragment, name="key_point")
        key_point_doc = aliased(Document, name="key_point_doc")
        # Analysis using this claim as a theme
        r = await session.execute(
            select(Analysis, source, source_doc
            ).join(source, source.id==Analysis.theme_id
            ).join(source_doc, source_doc.id == source.doc_id
            ).filter(cast(Analysis.params['theme'], Integer) == id
            ).options(joinedload(Analysis.analyzer)
            ).offset(offset).limit(limit))
        related_analysis = r.fetchall()

    next_ = (offset + limit) if len(related_analysis) == limit else ""
    prev = max(offset - limit, 0) if offset > 0 else ""
    # TODO: Probably exclude generated?
    outgoing_links = [(link.target_fragment, link) for link in claim.outgoing_links if link.target_fragment.is_claim]
    incoming_links = [(link.source_fragment, link) for link in claim.incoming_links if link.source_fragment.is_claim]
    # export if no parent, or at least one exported parent.
    can_export = not incoming_links or len([f for (f, l) in incoming_links if f.external_id])
    return await render_template(
        "claim_info.html", claim=claim, related_analysis=related_analysis, incoming_links=incoming_links,
        outgoing_links=outgoing_links, claim_nghd=claim_nghd,
        prev=prev, next=next_, offset=offset, limit=limit, prompt_analyzers=prompt_analyzers, can_export=can_export,
        **base_vars)


@app.route("/claim/<int:id>/add_related", methods=["POST"])
@app.route("/c/<collection>/claim/<int:id>/add_related", methods=["POST"])
@may_require_collection_permission('add_claim')
async def claim_add_related(id, collection=None):
    form = await request.form
    text = form.get("text")
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        collections = [collection] if collection else []
        if "add" not in form:
            # Navigating to another claim
            nghd = await claim_neighbourhood(id, session)
            claim = nghd['node']
            if not claim:
                raise NotFound()
            return await render_template(
                "propose_claim.html", text=text, node_type=form.get("node_type", "standalone_generic"), claim_nghd=nghd, **base_vars)
        # otherwise create the new claim
        new_claim = Fragment(text=text, scale=form.get("node_type"), position=0, char_position=0, language='en', collections=collections)
        new_link_type = form.get('link_type', 'freeform')
        if as_bool(form.get('reverse_link')):
            flink = ClaimLink(
                source_fragment=new_claim, target=id, link_type=new_link_type, created_by=current_user.auth_id)
        else:
            flink = ClaimLink(
                source=id, target_fragment=new_claim, link_type=new_link_type, created_by=current_user.auth_id)
        session.add(flink)
        await session.commit()
    return redirect(f'{collection.path}/claim/{new_claim.id}')


@app.route("/claim/<int:id>/search", methods=["GET", "POST"])
@app.route("/c/<collection>/claim/<int:id>/search", methods=["GET", "POST"])
@may_require_collection_permission('access')
async def search_on_claim(id, collection=None):
    if request.method == "POST":
        form = await request.form
        selection = update_fragment_selection(
            form.get("selection_changes"), as_bool(form.get("reset_fragments")))
        lam = float(form.get('lam_percent', None) or 50) / 100
        offset = int(request.args.get('offset') or form.get('offset') or 0)
        limit = int(request.args.get('limit') or form.get('limit') or 10)
        include_claims = as_bool(request.args.get('claim') or form.get('claim'))
        mode = request.args.get('mode') or form.get('mode') or "semantic"
        model = request.args.get('model') or form.get('model')
        include_paragraphs = as_bool(request.args.get('paragraph') or form.get('paragraph'))
    else:
        offset = int(request.args.get('offset') or 0)
        limit = int(request.args.get('limit') or 10)
        include_claims = as_bool(request.args.get('claim'))
        include_paragraphs = as_bool(request.args.get('paragraph'))
        mode = request.args.get('mode', "semantic")
        model = request.args.get('model')
        lam = 0.5
        selection = update_fragment_selection(None, as_bool(request.args.get('reset_fragments', True)))
    if not (include_paragraphs or include_claims):
        include_paragraphs = True
    prev = max(offset - limit, 0) if offset > 0 else ""
    async with Session() as session:
        prompt_analyzers = await session.execute(select(Analyzer.id, Analyzer.nickname).order_by(Analyzer.nickname).filter_by(name="fragment_prompt_analyzer"))
        prompt_analyzers = prompt_analyzers.all()
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        model = model or collection.embed_model()
        if model not in embed_models:
            raise BadRequest("Invalid model")
        Embedding = embed_models[model]
        q = select(Fragment, Embedding.analyzer_id
            ).join(Embedding, Fragment.id==Embedding.fragment_id
            ).join(Analyzer, Embedding.analyzer_id==Analyzer.id)
        q = q.filter(Fragment.id==id, Fragment.is_visible_claim
            ).order_by(Analyzer.version.desc()).limit(1)
        r = await session.execute(q)
        r = r.first()
        if not r:
            await get_channel("embed").send_soon(key=str(id), value=f"F{id} {model}")
            raise NotFound("Missing the embedding for this claim, try again shortly")
        claim, analyzer_id = r
        neighbour_embedding = aliased(Embedding, name="neighbour_embedding")
        neighbour = aliased(Fragment, name="neighbour")
        neighbour_doc = aliased(Document, name="neighbour_doc")
        neighbour_uri = aliased(UriEquiv, name="neighbour_uri")
        target = aliased(Embedding, name="target")
        key_point = aliased(Fragment, name="key_point")
        key_point_doc = aliased(Document, name="key_point_doc")
        key_point_uri = aliased(UriEquiv, name="key_point_uri")

        # keep in sync with search.py:
        # Fragment.doc_id, Fragment.id.label("fragment_id"), Document.url, Document.title, Fragment.position, tsrank, Fragment.text, Fragment.scale
        query = select(
                neighbour_embedding.fragment_id, neighbour.doc_id,
                neighbour.position, neighbour.text, neighbour.scale,
                key_point.id.label('key_point_id'), key_point.text.label('key_point_text'), key_point.position.label('key_point_position'),
                key_point_doc.id.label('key_point_doc_id'), key_point_doc.title.label('key_point_doc_title'), key_point_uri.uri.label('key_point_doc_url'),
            ).join(neighbour, neighbour.id==neighbour_embedding.fragment_id
            ).outerjoin(ClaimLink, ClaimLink.source==neighbour_embedding.fragment_id and ClaimLink.link_type == 'key_point'
            ).outerjoin(key_point, key_point.id==ClaimLink.target
            ).outerjoin(key_point_doc, key_point_doc.id==key_point.doc_id
            ).outerjoin(key_point_uri, key_point_uri.id==key_point_doc.uri_id)
        if include_paragraphs:
            query = query.join(neighbour_doc, neighbour_doc.id==neighbour.doc_id, isouter=include_claims
                        ).join(neighbour_uri, neighbour_doc.uri_id==neighbour_uri.id, isouter=include_claims
                        ).add_columns(neighbour_doc.title, neighbour_uri.uri)
        else:
            query = query.filter(neighbour.doc_id == None).add_columns(literal_column("null").label("title"), literal_column("null").label("uri"))
        scales = []
        if include_claims:
            scales.extend(visible_standalone_types)
            if include_paragraphs:
                scales.append("paragraph")
                if collection:
                    doc_coll = aliased(Collection, name="doc_coll")
                    claim_coll = aliased(Collection, name="claim_coll")
                    query = query.outerjoin(
                            claim_coll, neighbour.collections
                            ).outerjoin(doc_coll, neighbour_doc.collections
                            ).filter((doc_coll.name == collection.name) | (claim_coll.name == collection.name))
            elif collection:
                query = query.join(Collection, neighbour.collections).filter(Collection.name==collection.name)
        elif include_paragraphs:
            scales.append("paragraph")
            if collection:
                query = query.join(Collection, neighbour_doc.collections).filter(Collection.name==collection.name)
        if len(scales) > 1:
            query = query.filter(neighbour.scale.in_(scales))
        else:
            query = query.filter(neighbour.scale == scales[0])
        if mode == 'semantic':
            subq = select(target.embedding).filter_by(fragment_id=id, analyzer_id=analyzer_id).scalar_subquery()
            distance = neighbour_embedding.distance()(subq).label('rank')
            query = query.add_columns(distance).order_by(distance)
        elif mode == 'mmr':
            mmr = func.mmr(None, id, Embedding.__table__.name, scales, limit+offset, lam, 1000).table_valued("id", "score")
            query = query.join(mmr, mmr.columns.id == neighbour.id
                ).add_columns(mmr.columns.score.label('rank')).order_by(desc(mmr.columns.score))
        query = query.limit(limit).offset(offset)
        r = await session.execute(query)
        r = r.fetchall()
        next_ = (offset + limit) if len(r) == limit else ""
        return await render_template(
            "search.html", theme_id=id, text=claim.text, results=r, lam=lam, model=model,
            offset=offset, limit=limit, prev=prev, next=next_, selection=selection, include_paragraphs=include_paragraphs,
            include_claims=include_claims, models=list(embed_models.keys()), mode=mode, prompt_analyzers=prompt_analyzers, **base_vars)


@app.route("/claim/<int:theme_id>/gdelt", methods=["GET", "POST"])
@app.route("/c/<collection>/claim/<int:theme_id>/gdelt", methods=["GET", "POST"])
@may_require_collection_permission('bigdata_query')
async def gdelt(theme_id, collection=None):
    error = ''
    base_vars = await get_base_template_vars(current_user, collection)
    if request.method == "POST":
        try:
            form = await request.form
            limit = int(form.get("limit"))
            date = form.get('date') or None
            if date:
                # sanity
                date = datetime.strptime(date, '%Y-%m-%d')
                date = date.strftime('%Y-%m-%d')
            frequest = dict(claim=theme_id, limit= limit, since=date)
            await get_channel("gdelt").send_soon(key=str(theme_id), value=frequest)
            return redirect("/")
        except Exception as e:
            error = str(e)
    return await render_template("gdelt.html", claim_id=theme_id, error=error, **base_vars)



@app.route("/claim/<int:theme_id>/export_dm", methods=["POST"])
@app.route("/c/<collection>/claim/<int:theme_id>/export_dm", methods=["POST"])
@may_require_collection_permission('add_claim')
async def export_dm(theme_id, collection=None):
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        r = await session.execute(
            select(Fragment
            ).filter(Fragment.id==theme_id, Fragment.is_claim
            ).options(
                joinedload(Fragment.from_analysis).joinedload(Analysis.analyzer),
                subqueryload(Fragment.incoming_links).joinedload(ClaimLink.source_fragment)
            ).limit(1))
        claim = r.first()
        if not claim:
            raise NotFound()
        claim = claim[0]
        plink = None
        parent = None
        exported_parent = None
        if claim.external_id:
            raise BadRequest("Already exported")
        if claim.incoming_links:
            for link in claim.incoming_links:
                plink = link
                parent = link.source_fragment
                if parent.external_id:
                    exported_parent = parent
                if link.analyzer == claim.analysis.analyzer_id:
                    if not parent.external_id:
                        redirect(f"{collection.path}/claim/{parent.id}?error=Export the%20parent%20first")
                    break
            else:
                parent = None
                plink = None
            if not parent:
                parent = exported_parent
            if not parent:
                parent_id = claim.incoming_links[0].source
                redirect(f"{collection.path}/claim/{parent_id}?error=Export the%20parent%20first")
        await export_node(session, claim, collection, plink, parent)
        await session.commit()
    return redirect(f"{collection.path}/claim/{claim.id}")



@app.route("/c/<collection>/claim/<int:claim_id>/debatemap", methods=["GET"])
async def debatemap_to_claim(claim_id, root_id=None, collection=None):
    base_vars = await get_base_template_vars(current_user, collection)
    collection = base_vars['collection']
    async with Session() as session:
        query = select(Fragment.external_id).filter(Fragment.id==claim_id).limit(1)
        claim_eid = await session.scalar(query)
    root_id = collection.params.get('debatemap_node', None)
    if map := collection.params.get('debatemap_map', None):
        url = config.get("debatemap", "base_url") + f"map.{map}?s="
    depth = request.args.get("depth", type=int, default=8)
    if not root_id:
        raise BadRequest("Missing root")
    if not url:
        url = config.get("debatemap", "base_url")
    if depth == 1:
        return redirect(f"{url}{root_id}/{claim_id}")
    try:
        result = await debatemap_query(path_query, startNode=root_id, endNode=claim_eid)
        path = [n['nodeId'] for n in result['shortestPath']]
    except Exception:
        path = [claim_eid]
    return redirect(f"{url}{'/'.join(path)}")


@app.route("/claim/upload", methods=["GET", "POST"])
@app.route("/c/<collection>/claim/upload", methods=["GET", "POST"])
@may_require_collection_permission('add_claim')
async def upload_claims(collection=None):
    error = ""
    success = ""
    warning = ""
    repeats = []
    new_ids = []
    form = await request.form
    base_vars = await get_base_template_vars(current_user, collection)
    collection = base_vars['collection']
    if request.method == 'GET':
        return await render_template(
            "upload_claims.html", error='', success='', new_ids=[],
            **base_vars)
    if not request._files.get("file"):
        raise BadRequest("No file")
    files = await request.files
    fs = files.get("file")
    r = reader(TextIOWrapper(fs, "utf-8"))
    if as_bool(form.get("skip")):
        next(r)
    column = int(form.get("column")) - 1
    claim_texts = [row[column].strip() for row in r]
    node_type = form.get("node_type")
    claims = []
    async with Session() as session:
        for claim_text in claim_texts:
            # Check if it exists first
            existing = await session.execute(select(Fragment).filter(Fragment.text==claim_text, Fragment.scale.in_(standalone_type_names)).limit(1))
            if existing := existing.first():
                # Ensure in collection, right type
                repeats.append(existing.id)
                continue
            claim = Fragment(text=claim_text, scale=node_type, language="en", char_position=0, position=0, created_by=current_user.auth_id)
            if collection:
                claim.collections = [collection]
            session.add(claim)
            claims.append(claim)
        if claims:
            await session.commit()
            for claim in claims:
                # TODO Batch embedding requests
                await schedule_fragment_embeds([claim.id], [collection])
                new_ids.append(claim.id)
            success = f"Success, {len(new_ids)} created"
            if repeats:
                warning = f"Already existing: {repeats}"
        else:
            warning = "All those claim already exist"
    return await render_template(
        "upload_claims.html", error=error, success=success, warning=warning,
        new_ids=new_ids, **base_vars)
