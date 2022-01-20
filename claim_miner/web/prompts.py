"""
Copyright Society Library and Conversence 2022-2023
"""
from itertools import groupby
import re

from quart import render_template, request, redirect, Response
from sqlalchemy.orm import joinedload, subqueryload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.functions import count
from langchain import PromptTemplate
from werkzeug.exceptions import Unauthorized, BadRequest

from .. import Session, select, get_analyzer_id, as_bool
from . import update_fragment_selection, get_base_template_vars, schedule_fragment_embeds
from ..models import (Analyzer, Fragment, ClaimLink, FragmentCollection, Analysis, claim_neighbourhood)
from ..app import app, logger, qsession, current_user
from ..llm import get_base_llm, parsers_by_name, models, DEFAULT_MODEL
from ..auth import requires_permission, may_require_collection_permission
from ..debatemap_client import export_node


prompt_analyzer_names = ("fragment_prompt_analyzer", "simple_prompt_analyzer")

@app.route("/prompt")
@requires_permission('openai_query')
async def list_prompts():
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, None, session)
        q = select(
            Analyzer
          ).filter(Analyzer.name.in_(prompt_analyzer_names)).order_by(Analyzer.nickname)
        r = await session.execute(q)
        analyzers = [analyzer for (analyzer,) in r]

    return await render_template("list_prompts.html", analyzers=analyzers, **base_vars)


@app.route("/prompt/<nickname>", methods=['GET', 'POST'])
@requires_permission('openai_query')
async def show_edit_prompt(nickname):
    fragment_count = None
    errors = []
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, None, session)
        q = select(
                Analyzer, count(Fragment.id)
            ).filter(Analyzer.name.in_(prompt_analyzer_names), Analyzer.nickname==nickname
            ).outerjoin(Analysis, Analysis.analyzer_id==Analyzer.id
            ).outerjoin(Fragment, Fragment.analysis_id==Analysis.id
            ).group_by(Analyzer.id)
        r = await session.execute(q)
        (analyzer,fragment_count) = r.one()
        if request.method == 'POST':
            if not current_user.can('edit_prompts'):
                raise Unauthorized()
            form = await request.form
            if as_bool(form.get('clear_results')):
                analyzer.draft = True
            else:
                prompt = form.get('prompt')
                use_fragments=as_bool(form.get('use_fragments'))
                analyzer.nickname = form.get('nickname')
                draft = as_bool(form.get('draft'))
                prompt_args = re.findall(r'\{([^\}]+)\}', prompt)
                if set(prompt_args) - {'theme', 'fragments'}:
                    errors.append(f"Unknown prompt arguments: {prompt_args}")
                if 'theme' not in prompt_args:
                    errors.append('Missing {theme} reference in prompt')
                if use_fragments and ('fragments' not in prompt_args):
                    errors.append('Missing {fragments} reference in prompt')
                if not use_fragments and ('fragments' in prompt_args):
                    errors.append('There is a reference to {fragments} in prompt, but use_fragments is not set')
                analyzer.params = dict(
                    prompt=prompt,
                    parser=form.get('parser'),
                    node_type=form.get('node_type'),
                    link_type=form.get('link_type'),
                    model=form.get('model', DEFAULT_MODEL),  # analysis model
                    backwards_link=as_bool(form.get('backwards_link')),
                )
                analyzer.name = 'fragment_prompt_analyzer' if use_fragments else 'simple_prompt_analyzer'
                if draft and errors:
                    draft = True
                analyzer.draft = draft
                await session.commit()
    if nickname != analyzer.nickname:
        return redirect(f"/prompt/{analyzer.nickname}")
    return await render_template(
        "edit_prompt.html", error="\n".join(errors), analyzer=analyzer, fragment_count=fragment_count, models=models['openai'],
        **base_vars)


@app.route("/prompt", methods=['POST'])
@requires_permission('edit_prompts')
async def add_prompt():
    form = await request.form
    nickname = form.get('nickname')
    if not nickname:
        analyzer = Analyzer(params={}, name="simple_prompt_analyzer", draft=True, version=1)
        base_vars = await get_base_template_vars(current_user)
        return await render_template(
            "edit_prompt.html", analyzer=analyzer, fragment_count=0, models=models['openai'], **base_vars)
    async with Session() as session:
        analyzer = Analyzer(
            name='simple_prompt_analyzer', nickname=nickname, draft=True, version=1,
            params = dict(prompt=form.get('prompt'), node_type=form.get('node_type'), link_type=form.get('link_type')))
        session.add(analyzer)
        await session.commit()
    return redirect(f"/prompt/{nickname}")


@app.route("/claim/<int:theme_id>/simple_prompt", methods=["GET", "POST"])
@app.route("/c/<collection>/claim/<int:theme_id>/simple_prompt", methods=["GET", "POST"])
@app.route("/claim/<int:theme_id>/prompt_fragments", methods=["GET", "POST"])
@app.route("/c/<collection>/claim/<int:theme_id>/prompt_fragments", methods=["GET", "POST"])
@may_require_collection_permission('openai_query')
async def analyze_prompt(theme_id, collection=None):
    form = await request.form
    sources = []
    use_fragments = request.path.endswith('_fragments')

    # TODO: Does an analysis with those params already exist?
    # Decide: is model part of the analyzer or analysis? I say former.
    if use_fragments:
        sources = list(update_fragment_selection(
            form.get("selection_changes"), as_bool(form.get("reset_fragments"))))
        if not sources:
            raise BadRequest("No sources")
    analyzer_nickname = form.get("analyzer_nickname")
    smodel = form.get("model")  # This is the model used for semantic search
    analyzer_name = "fragment_prompt_analyzer" if use_fragments else "simple_prompt_analyzer"
    analyzer = await get_analyzer_id(analyzer_name, 1, nickname=analyzer_nickname, full=True)
    async with Session() as session:
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        r = await session.execute(select(Fragment).filter(Fragment.id.in_(sources+[theme_id])))
        fragments = {f.id: f for (f,) in r}
        theme = fragments.pop(theme_id)

    if use_fragments:
        prompt_t = PromptTemplate(
            input_variables=["theme", "fragments"], template=analyzer.params["prompt"])
            # partial_variables=dict(format_instructions=parser.get_format_instructions())
        fragment_texts = "\n\n".join(f"({id}): {f.text})" for (id, f) in fragments.items())
        prompt = prompt_t.format(theme=theme.text, fragments=fragment_texts)
    else:
        prompt_t = PromptTemplate(
            input_variables=["theme"], template=analyzer.params["prompt"])
        prompt = prompt_t.format(theme=theme.text)
    logger.info(prompt)
    llm = get_base_llm()  # model, temperature...
    resp = await llm.agenerate([prompt])
    result = resp.generations[0][0].text
    logger.info(result)
    parser = parsers_by_name[analyzer.params['parser']]
    result = parser.parse(result)
    logger.info(result)
    async with Session() as session:
        analysis = Analysis(
            analyzer = analyzer,
            theme = theme,
            results=result,
            params=dict(smodel=smodel, sources=sorted(fragments.keys())) if use_fragments else {},
            context=list(fragments.values())
        )
        session.add(analysis)
        await session.commit()

    return redirect(f'{collection.path}/analysis/{analysis.id}')


@app.route("/analysis/<int:analysis_id>", methods=["GET", "POST"])
@app.route("/c/<collection>/analysis/<int:analysis_id>", methods=["GET", "POST"])
@may_require_collection_permission('openai_query')
async def process_prompt_analysis(analysis_id, collection=None):
    form = await request.form
    changed = False
    async with Session() as session:
        analysis = await session.execute(
            select(Analysis
                ).filter_by(id=analysis_id
                ).options(
                    joinedload(Analysis.analyzer),
                    joinedload(Analysis.theme),
                    subqueryload(Analysis.context)))
        (analysis,) = analysis.one()
        analyzer = analysis.analyzer
        theme = analysis.theme
        theme_nghd = await claim_neighbourhood(theme.id, session)
        fragments = {f.id: f for f in analysis.context}
        for i, r in enumerate(analysis.results):
            new_text = form.get(f'text_{i+1}')
            if new_text and new_text.strip() != r['text'].strip():
                if 'old_text' not in r:
                    r['old_text'] = r['text']
                elif new_text == r['old_text']:
                    del r['old_text']
                r['text'] = new_text
                changed = True
        base_vars = await get_base_template_vars(current_user, collection, session)
        collection = base_vars['collection']
        collections = [collection] if collection else []
        r = await session.execute(
            select(Fragment, ClaimLink
            ).join(ClaimLink,
                ((Fragment.id==ClaimLink.source) & (ClaimLink.target==theme.id)) |
                ((Fragment.id==ClaimLink.target) & (ClaimLink.source==theme.id))))
        # Should I join with collection here?
        related_link = r.fetchall()
        outgoing_links = [(fragment, link) for (fragment, link) in related_link if link.source == theme.id and link.target_fragment.is_claim]
        incoming_links = [(fragment, link) for (fragment, link) in related_link if link.target == theme.id and link.source_fragment.is_claim]
        related_nodes = {n.id: n for (n, l) in related_link}

        if saving:= form.get("saving"):
            new_standalone_data = analysis.results[int(saving)-1]
            if new_standalone_data.get('fragment_id', None):
                raise BadRequest("Already saved")
            new_node_type = analyzer.params['node_type']
            new_link_type = analyzer.params['link_type']
            generation_data = {}
            if fragments:
                # We had a stack trace here, with sources undefined. How?
                sources = new_standalone_data.get('sources', [])
                if sources:
                    generation_data['sources'] = sources
                else:
                    logger.warning("Missing sources! %s", new_standalone_data)
            if 'old_text' in new_standalone_data:
                generation_data['old_text'] = new_standalone_data['old_text']
            fragment = Fragment(
                text=new_standalone_data['text'], scale=new_node_type, position=0, char_position=0, language='en',
                from_analysis=analysis, generation_data=generation_data, created_by=current_user.auth_id)
            if fragments:
                slinks = [
                    ClaimLink(target_fragment=fragment, source_fragment=fragments[source], link_type='key_point',
                        analyzer=analyzer.id, created_by=current_user.auth_id)
                    for source in sources
                ]
                session.add_all(slinks)
            if analyzer.params.get('backwards_link', False):
                flink = ClaimLink(
                    source_fragment=fragment, target_fragment=theme, link_type=new_link_type,
                    analyzer=analyzer.id, created_by=current_user.auth_id)
            else:
                flink = ClaimLink(
                    source_fragment=theme, target_fragment=fragment, link_type=new_link_type,
                    analyzer=analyzer.id, created_by=current_user.auth_id)
            session.add(flink)
            await session.flush()
            if collection:
                session.add(FragmentCollection(collection_id=collection.id, fragment_id=fragment.id))
            new_standalone_data['fragment_id'] = fragment.id
            analysis.results[int(saving)-1] = new_standalone_data
            await session.commit()
            changed = True
            related_nodes[fragment.id] = fragment
            await schedule_fragment_embeds([fragment.id], [collection])

        elif exporting:= form.get("exporting"):
            if not theme.external_id:
                raise BadRequest("Parent should already be exported")
            fragment = related_nodes[analysis.results[int(exporting)-1]['fragment_id']]
            if fragment.external_id:
                raise BadRequest("Already exported")
            session.add(fragment)  # re-attach
            await export_node(session, fragment, collection, analyzer.params['link_type'], theme)
            # TODO: trigger sync... May require finding a path to the root.
            changed = True

        if changed:
            session.add(analysis)  # re-attach
            flag_modified(analysis, 'results')
        await session.commit()

    return await render_template(
        "apply_prompt.html", analyzer=analyzer, analysis=analysis, theme=theme, outgoing_links = outgoing_links,
        incoming_links=incoming_links, related_nodes=related_nodes, fragments=fragments, theme_nghd=theme_nghd,
        **base_vars)
