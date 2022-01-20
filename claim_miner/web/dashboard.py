"""
Copyright Society Library and Conversence 2022-2023
"""
from quart import render_template
from sqlalchemy.sql import distinct
from sqlalchemy.sql.functions import count
from sqlalchemy.orm import aliased

from .. import Session, select
from ..models import Document, Fragment, embed_models, Analysis, ClaimLink, globalScope
from ..app import app, login_required, logger, current_user
from . import get_base_template_vars


def emb_by_model(row):
    return {model: getattr(row, f'emb_{model}') for model in embed_models}

@app.route("/")
@login_required
async def dashboard():
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, None, session)
        q = select(
              count(Document.id).label("documents"),
              Document.load_status)
        groups = [Document.load_status]
        for model, Embedding in embed_models.items():
            embedding = aliased(Embedding, name=f"emb_{model}")
            q = q.outerjoin(embedding, (embedding.doc_id==Document.id) & (embedding.fragment_id==None))
            q = q.add_columns(count(embedding.doc_id).label(f"emb_{model}"))
            groups.append(embedding.doc_id == None)
        q = q.group_by(*groups)
        logger.debug(q.selectable.compile(dialect=session.bind.dialect))
        r = await session.execute(q)
        doc_data = [(row, emb_by_model(row)) for row in r]

        source_in = aliased(ClaimLink, name="source_in")
        result_in = aliased(ClaimLink, name="result_in")
        q = select(
              Fragment.scale.label("scale"),
              count(Fragment.id).label("fragments"),
              count(Analysis.theme_id).label("analyzed"),
              count(distinct(Analysis.theme_id)).label("analyzed_distinct"),
              count(source_in.source).label("as_sources"),
              count(result_in.target).label("as_results"),
              count(distinct(source_in.source)).label("as_sources_distinct"),
              count(distinct(result_in.target)).label("as_results_distinct"),
            ).outerjoin(Analysis, Analysis.theme_id==Fragment.id
            ).outerjoin(source_in, source_in.source==Fragment.id
            ).outerjoin(result_in, result_in.target==Fragment.id)
        for model, Embedding in embed_models.items():
            embedding = aliased(Embedding, name=f"emb_{model}")
            q = q.outerjoin(embedding, embedding.fragment_id==Fragment.id)
            q = q.add_columns(count(embedding.fragment_id).label(f"emb_{model}"))
        q = q.group_by(Fragment.scale).where(Fragment.scale != "document")

        r = await session.execute(q)
        fragment_data = [(row, emb_by_model(row)) for row in r]

        return await render_template("home.html", doc_data=doc_data, fragment_data=fragment_data, models=list(embed_models.keys()), **base_vars)
