"""
Copyright Society Library and Conversence 2022-2023
"""
# clustering
from collections import defaultdict
from html import escape

import numpy as np
import simplejson as json
from sqlalchemy.future import select
from sklearn.metrics.pairwise import cosine_similarity
import sklearn.manifold
import sklearn.decomposition
from quart import request, render_template
from quart.utils import run_sync
from werkzeug.exceptions import BadRequest

from .. import Session
from ..models import Fragment, embed_models, Collection, FragmentCollection, BASE_EMBED_MODEL
from ..app import app, logger, current_user
from ..embed import tf_embed
from ..auth import may_require_collection_permission
from ..debatemap_client import descendants_query, debatemap_query
from . import get_base_template_vars


@app.route("/claim_index/<claim_id>/scatter", methods=["GET"])
@app.route("/claim/scatter", methods=["GET"])
@app.route("/c/<collection>/claim_index/<claim_id>/scatter", methods=["GET"])
@app.route("/c/<collection>/claim/scatter", methods=["GET"])
@may_require_collection_permission('access')
async def claim_scatter(claim_id=None, collection=None):
    model = request.args.get('model', BASE_EMBED_MODEL)
    if model not in embed_models:
        raise BadRequest("Invalid model")
    Embedding = embed_models[model]
    base_vars = await get_base_template_vars(current_user, collection)
    if claim_id:
        try:
            claim_id = int(claim_id)
            external_id = False
        except ValueError:
            external_id = True
        depth = request.args.get("depth", type=int, default=6)
        query = select(Fragment).filter_by(scale="standalone_root")
        if external_id:
            query = query.filter_by(external_id=claim_id)
        else:
            query = query.filter_by(id=claim_id)
        query = query.limit(1)
        async with Session() as session:
            r = await session.execute(query)
            (claim,) = r.one()
            debatemap_base = claim.external_id
        result = await debatemap_query(descendants_query, nodeId=claim.external_id, depth=depth)
        node_ids = {node['id'] for node in result["descendants"]}
        node_ids.discard(claim.external_id)
        if node_ids:
            async with Session() as session:
                query = select(
                    Embedding.fragment_id, Embedding.embedding, Fragment.text, Fragment.external_id
                    ).join(Fragment).filter(Fragment.external_id.in_(node_ids))
                if (collection):
                    query = query.join(FragmentCollection).join(Collection).filter(Collection.name == collection)
                data = await session.execute(query)
        else:
            data = []
    else:
        debatemap_base = None
        query = select(
            Embedding.fragment_id, Embedding.embedding, Fragment.text, Fragment.external_id
        ).join(Fragment).filter(Fragment.is_visible_claim)
        if (collection):
            query = query.join(FragmentCollection).join(Collection).filter(Collection.name == collection)
        async with Session() as session:
            data = await session.execute(query)
    (fids, embeds, texts, external_ids) = zip(*data)
    embeds = list(embeds)
    num_claims = len(fids)
    keywords = request.args.getlist("keyword") or []
    if len(keywords)==1:
        keywords = [t.strip() for t in keywords[0].split(",")]
    if keywords:
        # TODO: cache keyword embed results to avoid recurring costs
        kwembeds = await tf_embed(keywords, model)
        all_embeds = embeds + kwembeds
        similarities = await run_sync(lambda: cosine_similarity(embeds, kwembeds).tolist())()
        num_total = len(all_embeds)
    else:
        all_embeds = embeds
        similarities = []
        num_total = num_claims
    method_name = request.args.get("method", default="TruncatedSVD")
    kwargs = dict(n_components=2)
    if hasattr(sklearn.manifold, method_name):
        method_class = getattr(sklearn.manifold, method_name)
        kwargs["n_jobs"] = -1
        kwargs |= defaultdict(dict, dict(
          LocallyLinearEmbedding=dict(method="hessian", n_neighbors=6, eigen_solver='dense'),
          SpectralEmbedding=dict(eigen_solver="amg"),  # affinity="rbf"
          ))[method_name]
    elif hasattr(sklearn.decomposition, method_name):
        method_class = getattr(sklearn.decomposition, method_name)
    else:
        raise BadRequest("Unknown method")
    method = method_class(**kwargs)
    pos = await run_sync(lambda: method.fit_transform(np.array(all_embeds)))()
    claims_data = [
        dict(id=id, x=float(x), y=float(y), t=escape(t))
        for (id, t, (x, y)) in zip(fids, texts, pos[:num_claims])]
    keyword_data = [
        dict(id=n, x=float(x), y=float(y), t=escape(t))
        for ((n, t), (x, y)) in zip(enumerate(keywords), pos[num_claims:])]
    return await render_template(
        "scatter.html",
        data=json.dumps(claims_data),
        method=method_name,
        debatemap_base=debatemap_base,
        keywords=", ".join(keywords),
        similarities=json.dumps(similarities),
        external_ids=json.dumps(dict(zip(fids, external_ids))),
        model=model,
        keyword_data=json.dumps(keyword_data),
        **base_vars
    )
