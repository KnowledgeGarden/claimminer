"""
Copyright Society Library and Conversence 2022-2023
"""
# clustering
from collections import defaultdict

import numpy as np
from sqlalchemy.future import select
from sklearn.cluster import DBSCAN
from quart import request, render_template, Response
from quart.utils import run_sync
from werkzeug.exceptions import BadRequest

from . import get_base_template_vars
from .. import Session
from ..app import app, logger, current_user
from ..auth import may_require_collection_permission
from ..models import Fragment, Collection, embed_models, BASE_EMBED_MODEL

@app.route("/claim/clusters", methods=["GET"])
@app.route("/c/<collection>/claim/clusters", methods=["GET"])
@may_require_collection_permission('access')
async def claim_cluster(collection=None):
    eps = request.args.get("eps", type=float, default=0.25)
    min_samples = request.args.get("min_samples", type=int, default=8)
    model = request.args.get('model') or BASE_EMBED_MODEL
    if model not in embed_models:
        raise BadRequest("Invalid model")
    Embedding = embed_models[model]
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        q = select(
            Embedding.fragment_id, Embedding.embedding, Fragment.text
        ).join(Fragment).filter(Fragment.is_visible_claim)
        if collection:
            q = q.join(Collection, Fragment.collections).filter(Collection.name==collection)
        data = await session.execute(q)
    data = list(zip(*data))
    clusters = defaultdict(list)
    if data:
        (fids, embeds, texts) = data
        embeds = np.array(embeds)
        scan = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine")
        db = await run_sync(scan.fit)(embeds)
        for i, c in enumerate(db.labels_):
            if c == -1:
                continue
            clusters[c].append((fids[i], texts[i]))
        missing = len(db.labels_) - sum(len(c) for c in clusters.values())
    else:
        missing = 0
    return await render_template(
          "claim_clusters.html", clusters=clusters.values(), missing=missing, eps=eps,
          min_samples=min_samples, model=model, **base_vars)
