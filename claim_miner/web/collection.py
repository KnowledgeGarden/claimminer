"""
Copyright Society Library and Conversence 2022-2023
"""
from quart import request, render_template, redirect
from sqlalchemy.sql.functions import count, coalesce

from .. import Session, select, hashfs, as_bool
from ..models import DocCollection, FragmentCollection, Collection, CollectionPermissions, globalScope, OPENAI_EMBED_MODEL
from ..app import app, login_required, current_user, logger, requires_permission
from . import get_base_template_vars
from ..debatemap_client import getAccessPolicies

@app.route("/c")
@login_required
async def list_collections():
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, None, session)
        q1 = select(
            Collection, CollectionPermissions
          ).outerjoin(CollectionPermissions, (CollectionPermissions.collection_id==Collection.id)
            & (CollectionPermissions.user_id == current_user.auth_id)
          )
        q2 = select(
            Collection.id, Collection.name, count(DocCollection.doc_id.distinct()), count(FragmentCollection.fragment_id.distinct())
          ).outerjoin(DocCollection, DocCollection.collection_id == Collection.id
          ).outerjoin(FragmentCollection, FragmentCollection.collection_id == Collection.id
          ).group_by(Collection.id)
        r1 = list(await session.execute(q1))
        r2 = await session.execute(q2)
        counts = {id: (numdoc, numfrag) for (id, name, numdoc, numfrag) in r2}
        data = [(coll, *counts[coll.id]) for coll, perms in r1]
        collection_names = [c[1] for c in r2]
        for coll, perm in r1:
            coll.user_permissions = perm
        logger.debug(data)
    return await render_template("list_collections.html", data=data, **base_vars)


@app.route("/c/<collection>", methods=["GET", "POST"])
@login_required
async def show_collection(collection):
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        q2 = select(
            Collection.id, count(DocCollection.doc_id.distinct()), count(FragmentCollection.fragment_id.distinct())
          ).outerjoin(DocCollection, DocCollection.collection_id == Collection.id
          ).outerjoin(FragmentCollection, FragmentCollection.collection_id == Collection.id
          ).filter(Collection.id==collection.id).group_by(Collection.id)
        r2 = await session.execute(q2)
        (_, num_docs, num_frags) = r2.one()
        if request.method == "POST":
            form = await request.form
            params = dict(collection.params)
            if as_bool(form.get("ada2")):
                params['embeddings'] = list(set(params.get('embeddings', [])+[OPENAI_EMBED_MODEL]))
            else:
                params['embeddings'] = list(set(params['embeddings'])-{OPENAI_EMBED_MODEL})
            params['export_debatemap'] = export_debatemap = as_bool(form.get("export_debatemap", ''))
            if export_debatemap:
                params['debatemap_map'] = form.get('debatemap_map', None)
                params['debatemap_node'] = form.get('debatemap_node', None)
                params['debatemap_policy'] = form.get('debatemap_policy', None)
            collection.params = params
            await session.commit()
    policies = await getAccessPolicies()
    return await render_template("view_collection.html", num_docs=num_docs, num_frags=num_frags, access_policies=policies, **base_vars)


@app.route("/c", methods=["POST"])
@requires_permission('admin')
async def add_collection():
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        form = await request.form
        collection = Collection(name=form.name)
        if as_bool(form.get("ada2")):
            collection.params['embeddings'] = [OPENAI_EMBED_MODEL]
        session.add(collection)
        await session.commit()
    return await redirect(collection.collection_path)
