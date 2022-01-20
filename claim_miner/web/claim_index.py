"""
Copyright Society Library and Conversence 2022-2023
"""
import re

# clustering
from sqlalchemy.future import select
from quart import request, render_template, redirect, Response
from werkzeug.exceptions import BadRequest

from ..app import app, get_channel, logger, current_user
from ..debatemap_client import descendants_query, path_query, debatemap_query
from ..models import Fragment, Document, UriEquiv
from .. import Session, as_bool, config
from ..auth import may_require_collection_permission
from . import get_base_template_vars, fragment_collection_constraints


@app.route("/claim_index", methods=["GET"])
@app.route("/c/<collection>/claim_index", methods=["GET"])
@may_require_collection_permission('access')
async def list_claim_indexes(collection=None):
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        query = select(Fragment).filter_by(scale="standalone_root"
            ).order_by(Fragment.text)
        if collection or not await current_user.can('access'):
            query = await fragment_collection_constraints(query, collection)
        r = await session.execute(query)
        claims = [c for (c,) in r.fetchall()]
    return await render_template(
        "list_claims.html", claims=[], claim_indices=claims, offset=0, prev=0, next=0, limit=30, end=30,
        **base_vars)

@app.route("/claim_index/<int:claim_id>", methods=["GET"])
@app.route("/c/<collection>/claim_index/<int:claim_id>", methods=["GET"])
@may_require_collection_permission('access')
async def show_claim_index(claim_id, collection=None):
    depth = request.args.get("depth", type=int, default=None)
    reload = as_bool(request.args.get("reload"))
    offset = request.args.get("start", type=int, default=0)
    limit = request.args.get("limit", type=int, default=15)
    if depth is None:
        if 'direct' in request.args:
            depth = 1
        elif 'descendants' in request.args:
            depth = 6
        else:
            logger.error("Error: no depth specified")
            depth = 1
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        query = select(Fragment).filter_by(scale="standalone_root", id=claim_id).limit(1)
        # Should I join with collection?
        r = await session.execute(query)
        (claim,) = r.one()
    if reload:
        await get_channel("debatemap").send_soon(key=str(claim.id), value=f"{claim.external_id} {depth}")
        logger.debug("after send debatemap")
    result = await debatemap_query(descendants_query, nodeId=claim.external_id, depth=depth)
    node_ids = {node['id'] for node in result["descendants"]}
    node_ids.discard(claim.external_id)
    if node_ids:
        async with Session() as session:
            query = select(Fragment).filter(Fragment.external_id.in_(node_ids)).order_by(Fragment.text).offset(offset).limit(limit)
            r = await session.execute(query)
            fragments = [f for (f,) in r]
    previous = max(offset - limit, 0) if offset > 0 else ""
    next_ = (offset + limit) if len(fragments) == limit else ""
    end = offset + len(fragments)
    return await render_template(
        "claim_index.html", ci=claim, fragments=fragments,
        depth=depth, offset=offset, prev=previous, next=next_, limit=limit, end=end, **base_vars)


@app.route("/claim_index/<string:root_id>/debatemap/<string:claim_id>", methods=["GET"])
@app.route("/c/<collection>/claim_index/<string:root_id>/debatemap/<string:claim_id>", methods=["GET"])
async def debatemap_to(root_id, claim_id, collection=None):
    base_vars = await get_base_template_vars(current_user, collection)
    collection = base_vars['collection']
    async with Session() as session:
        query = select(UriEquiv.uri).join(Document).join(Fragment).filter(Fragment.external_id==root_id).limit(1)
        url = await session.scalar(query)
    if not url and collection:
        if map := collection.params.get('debatemap_map', None):
            url = config.get("debatemap", "base_url") + f"map.{map}?s="
    if not url:
        url = config.get("debatemap", "base_url")
    depth = request.args.get("depth", type=int, default=8)
    if depth == 1:
        return redirect(f"{url}{root_id}/{claim_id}")
    result = await debatemap_query(path_query, startNode=root_id, endNode=claim_id)
    path = [n['nodeId'] for n in result['shortestPath']]
    return redirect(f"{url}{'/'.join(path)}")


@app.route("/c/<collection>/claim_index", methods=["POST"])
async def add_claim_index(collection):
    base_vars = await get_base_template_vars(current_user, collection)
    collection = base_vars['collection']
    form = await request.form
    node_id = form.get("node_id")
    map_id = form.get("map_id")
    map_nickname = form.get("map_nickname")
    if not node_id or not map_id or not map_nickname:
        raise BadRequest("Missing data")
    map_slug = "_".join(re.split(r'\W', map_nickname.strip().lower()))
    base_url = config.get("debatemap", "base_url")
    url = f'{base_url}/debates/{map_slug}.{map_id}?s='
    doc = Document(url=url, collections=[collection])
    node = Fragment(scale='standalone_root', external_id=node_id, document=doc, collections=[collection], position=0, char_position=0, language='en', text=map_nickname)
    async with Session() as session:
        session.add(node)
        await session.commit()
    await get_channel("debatemap").send_soon(key=str(node_id), value=f"{node_id} 8")
    return redirect(f'{collection.path}/claim_index/{node.id}')
