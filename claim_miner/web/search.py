"""
Copyright Society Library and Conversence 2022-2023
"""
from sqlalchemy.future import select
from sqlalchemy.sql import desc, cast, func, literal_column
from sqlalchemy.orm import aliased
from sqlalchemy import cast, func
from pgvector.sqlalchemy import Vector
from quart import request, render_template, jsonify
from quart_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.exceptions import Unauthorized, BadRequest

from .. import Session, as_bool
from ..models import (
    en_regconfig, Document, Fragment, Analyzer, UriEquiv, Collection, embed_models,
    visible_standalone_types, BASE_EMBED_MODEL)
from ..app import app, logger, current_user
from ..embed import tf_embed
from ..auth import may_require_collection_permission, set_user
from . import update_fragment_selection, get_collections_and_scope, get_base_template_vars


@app.route("/search")
@app.route("/claim/propose")
@app.route("/c/<collection>/search")
@app.route("/c/<collection>/claim/propose")
@may_require_collection_permission('access')
async def search_form(collection=None):
    update_fragment_selection(None, True)
    base_vars = await get_base_template_vars(current_user, collection)
    collection = base_vars['collection']
    is_proposal = request.path.split('/')[-1] == 'propose'
    return await render_template(
        "search.html", theme_id=None, include_paragraphs=True, is_proposal=is_proposal,
        mode="semantic", lam=0.5, models=list(embed_models.keys()), model=collection.embed_model(), **base_vars)


@app.route("/search", methods=['POST'])
@app.route("/claim/propose", methods=['POST'])
@app.route("/c/<collection>/search", methods=['POST'])
@app.route("/c/<collection>/claim/propose", methods=['POST'])
@may_require_collection_permission('access')
async def search(collection=None):
    is_proposal = request.path.split('/')[-1] == 'propose'
    form = await request.form
    lam = float(form.get('lam_percent', None) or 50) / 100
    text = form.get('text')
    mode = form.get('mode') or "semantic"
    include_claims = as_bool(form.get('claim'))
    include_paragraphs = as_bool(form.get('paragraph'))
    offset = int(request.args.get('offset') or form.get('offset') or 0)
    limit = int(request.args.get('limit') or form.get('limit') or 10)
    model = form.get('model', None)
    selection = update_fragment_selection(
        form.get("selection_changes"), as_bool(form.get("reset_fragments")))
    if is_proposal:
        include_claims = True
        include_paragraphs = False
        model = None  # will be the collection's model
        mode = "semantic"

    if not (include_paragraphs or include_claims):
        base_vars = await get_base_template_vars(current_user, collection)
        return await render_template(
            "search.html", theme_id=None, text=text, mode=mode, results=[], offset=0, error="Nothing to search for",
            limit=limit, lam=lam, prev="", next="", end=0, selection=selection, include_claims=include_claims,
            include_paragraphs=include_paragraphs, models=list(embed_models.keys()), **base_vars)

    prev = max(offset - limit, 0) if offset > 0 else ""
    scales = []
    if include_claims:
        scales.extend(visible_standalone_types)
    if include_paragraphs:
        scales.append('paragraph')
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        # TODO: Check whether user can propose, now that we have the collection
        model = model or collection.embed_model()
        if model not in embed_models:
            raise BadRequest("Invalid model")
        Embedding = embed_models[model]
        prompt_analyzers = await session.execute(select(Analyzer.id, Analyzer.nickname).order_by(Analyzer.nickname).filter_by(name="fragment_prompt_analyzer"))
        prompt_analyzers = prompt_analyzers.all()
        # Keep in sync with search_on_claim
        query = select(Fragment.doc_id, Fragment.id.label("fragment_id"), Fragment.position, Fragment.text, Fragment.scale)
        if mode != 'text':
            query = query.join(Embedding, Embedding.fragment_id==Fragment.id)
        if include_paragraphs:
            query = query.join(Document, Document.id==Fragment.doc_id, isouter=include_claims
                        ).join(UriEquiv, Document.uri_id==UriEquiv.id, isouter=include_claims
                        ).add_columns(UriEquiv.uri, Document.title)
        else:
            query = query.filter(Fragment.doc_id == None).add_columns(literal_column("null").label("uri"), literal_column("null").label("title"))
        if collection:
            if include_claims:
                if include_paragraphs:
                    doc_coll = aliased(Collection, name="doc_coll")
                    claim_coll = aliased(Collection, name="claim_coll")
                    query = query.outerjoin(
                            claim_coll, Fragment.collections
                            ).outerjoin(doc_coll, Document.collections
                            ).filter((doc_coll.name == collection.name) | (claim_coll.name == collection.name))
                else:
                    query = query.join(Collection, Fragment.collections).filter(Collection.name==collection.name)
            elif include_paragraphs:
                query = query.join(Collection, Document.collections).filter(Collection.name==collection.name)

        if len(scales) > 1:
            query = query.filter(Fragment.scale.in_(scales))
        else:
            query = query.filter(Fragment.scale == scales[0])
        if mode == 'text':
            tsquery = func.websearch_to_tsquery(en_regconfig, text).label('tsquery')
            vtext = func.to_tsvector(Fragment.text)
            tsrank = func.ts_rank_cd(vtext, tsquery).label('rank')
            query = query.add_columns(tsrank)
            query = query.filter(func.starts_with(Fragment.language, 'en')).filter(Fragment.ptmatch('english')(tsquery)).order_by(desc(tsrank))
        else:
            text_embed = await tf_embed(text, model)
            if mode == 'semantic':
                rank = Embedding.distance()(text_embed).label('rank')
                query = query.add_columns(rank).order_by(rank)
            elif mode == 'mmr':
                mmr = func.mmr(cast(text_embed, Vector), None, Embedding.__table__.name, scales, limit+offset, lam, 1000).table_valued("id", "score")
                query = query.join(mmr, mmr.columns.id == Fragment.id
                    ).add_columns(mmr.columns.score).order_by(desc(mmr.columns.score))
            else:
                raise BadRequest("Unknown mode")
        query = query.limit(limit).offset(offset)
        r = await session.execute(query)
        r = r.fetchall()
    next_ = (offset + limit) if len(r) == limit else ""
    end = offset + len(r)

    return await render_template(
        "search.html", theme_id=None, text=text, mode=mode, results=r, offset=offset, is_proposal=is_proposal,
        limit=limit, lam=lam, prev=prev, next=next_, end=end, selection=selection, include_claims=include_claims,
        include_paragraphs=include_paragraphs, model=model, models=list(embed_models.keys()), prompt_analyzers=prompt_analyzers, **base_vars)


@app.route("/api/search", methods=['POST'])
@app.route("/api/c/<collection>/search", methods=["POST"])
@jwt_required
async def search_json(collection=None):
    current_user = await set_user(get_jwt_identity())
    json = await request.json
    if not json:
        raise BadRequest("Please post JSON")
    text = json['text']
    offset = json.get("offset", 0)
    limit = json.get("limit", 20)
    mode = json.get("mode", "semantic")
    search_paras = as_bool(json.get("search_paragraphs", ""))
    if mode not in ("semantic", "mmr"):
        raise BadRequest("mode must be one of semantic or mmr")
    if mode == "mmr":
        lam = json.get("lambda", 0.7)
        if not isinstance(lam, float):
            raise BadRequest("lambda must be a float")
        if not 0 <= lam <= 1:
            raise BadRequest("lambda must be between 0 and 1")
    async with Session() as session:
        collections, collection = await get_collections_and_scope(json.get('collection', collection), user_id=current_user.auth_id)
        can_see = await collection.user_can(current_user, 'access')
        if not can_see:
            return Unauthorized()
        scales = list(visible_standalone_types)
        model = json.get("model", collection.embed_model())
        if model not in embed_models:
            raise BadRequest("Invalid model")
        Embedding = embed_models[model]
        if search_paras:
            query = select(Fragment.doc_id, Fragment.id, Fragment.position, Fragment.text, UriEquiv.uri.label('url'), Document.title
                ).join(Document, Document.id==Fragment.doc_id
                ).join(UriEquiv, Document.uri_id==UriEquiv.id
                ).filter(Fragment.scale=='paragraph')
            if collections:
                query = query.join(Collection, Document.collections).filter(Collection.name.in_([c.name for c in collections]))
        else:
            query = select(Fragment.id, Fragment.text, Fragment.scale.label("node_type")).filter(Fragment.scale.in_(visible_standalone_types))
            if collections:
                query = query.join(Collection, Fragment.collections).filter(Collection.name.in_([c.name for c in collections]))

        query = query.join(Embedding, Embedding.fragment_id==Fragment.id)
        text_embed = await tf_embed(text, model)
        if mode == 'semantic':
            rank = Embedding.distance()(text_embed).label('rank')
            query = query.add_columns(rank).order_by(rank)
        elif mode == 'mmr':
            mmr = func.mmr(cast(text_embed, Vector), None, Embedding.__table__.name, scales, limit+offset, lam, 1000).table_valued("id", "score")
            query = query.join(mmr, mmr.columns.id == Fragment.id
                ).add_columns(mmr.columns.score).order_by(desc(mmr.columns.score))
        query = query.limit(limit).offset(offset)
        r = await session.execute(query)
        return jsonify([row._asdict() for row in r])
