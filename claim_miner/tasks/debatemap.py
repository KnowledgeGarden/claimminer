"""
Copyright Society Library and Conversence 2022-2023
"""
import re
from itertools import chain

from sqlalchemy import delete
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from .. import Session
from ..debatemap_client import debatemap_query, node_data_query, convert_node_type, convert_link_type
from ..models import Fragment, Embedding, ClaimLink, embed_models
from . import logger, schedule_fragment_embeds

cleanup_emoji_re = re.compile(r"[\U0001f300-\U0001f5ff\U0001f900-\U0001f9ff\U0001f600-\U0001f64f\U0001f680-\U0001f6ff\u2600-\u26ff\u2700-\u27bf\U0001f1e6-\U0001f1ff\U0001f191-\U0001f251\U0001f004\U0001f0cf\U0001f170-\U0001f171\U0001f17e-\U0001f17f\U0001f18e\u3030\u2b50\u2b55\u2934-\u2935\u2b05-\u2b07\u2b1b-\u2b1c\u3297\u3299\u303d\u00a9\u00ae\u2122\u23f3\u24c2\u23e9-\u23ef\u25b6\u23f8-\u23fa\ufe0f\u200d]")
cleanup_notes_re = re.compile(r"\[[^\[\]]*\]")

def cleanup_sentence(txt):
    txt = cleanup_emoji_re.sub('', txt)
    txt = cleanup_notes_re.sub('', txt)
    return txt.strip()


async def do_debatemap(base_eid, depth):
    response = await debatemap_query(node_data_query, nodeId=base_eid, depth=depth)
    response = response['subtree']
    nodes = {n['id']: n for n in response['nodes']}
    phrasings_by_node_id = {p['node']: p for p in response['nodePhrasings']}
    rev_by_id = {r['id']: r for r in response['nodeRevisions']}
    rev_by_node_id = {id: rev_by_id[n['c_currentRevision']] for (id, n) in nodes.items()}
    links = {l['id']: l for l in response['nodeLinks']}
    texts_by_id = {id: phrasing.get('text_question', None) or phrasing.get('text_base', None)
                for (id, phrasing) in phrasings_by_node_id.items()
            } | {id: rev['phrasing'].get('text_question', None) or rev['phrasing'].get('text_base', None)
                for (id, rev) in rev_by_node_id.items()}
    texts_by_id = {k: cleanup_sentence(v) for (k, v) in texts_by_id.items()}
    # texts_by_id = {k: v for k, v in texts_by_id if v}
    node_types = {id: convert_node_type(node['type'], texts_by_id[id], node['multiPremiseArgument']) for (id, node) in nodes.items()}
    async with Session() as session:
        r = await session.execute(
            select(Fragment
            ).filter(Fragment.external_id.in_(nodes.keys())
            ).options(joinedload(Fragment.collections)))
        existing_nodes = {f.external_id: f for (f,) in r.unique()}
        base_node = existing_nodes[base_eid]
        collections = list(base_node.collections)
        collections_set = set(collections)
        for id, fr in existing_nodes.items():
            if id == base_eid:
                continue
            fr.scale = node_types[id]
            for c in collections_set - set(fr.collections):
                fr.collections.append(c)
        different_nodes = [f for f in existing_nodes.values() if f.text != texts_by_id[f.external_id] and f.external_id != base_eid]
        if different_nodes:
            for Embedding in embed_models.values():
                await session.execute(
                    delete(Embedding).where(
                        Embedding.fragment_id.in_((f.id for f in different_nodes))))
            for f in different_nodes:
                f.text = texts_by_id[f.external_id]
            different_nodes = [f.id for f in different_nodes]
        missing_nodes = [
            Fragment(text=txt, scale=node_types[eid], external_id=eid, char_position=0, language='en', position=0, collections=collections)
            for eid, txt in texts_by_id.items()
            if eid not in existing_nodes
        ]
        if missing_nodes:
            session.add_all(missing_nodes)
            await session.flush()
        # TODO: Look at claims that were connected through that sync and that are now obsolete.
        # I think the request as written is cross-map?
        all_nodes = existing_nodes | {f.external_id: f for f in missing_nodes}
        missing_nodes = [f.id for f in missing_nodes]
        r = await session.execute(
            select(ClaimLink).filter(ClaimLink.external_id.in_(links.keys())))
        existing_ext_links = {l.external_id: l for (l,) in r}
        existing_node_ids = [n.id for n in existing_nodes.values()]
        r = await session.execute(
            select(ClaimLink).filter(ClaimLink.source.in_(existing_node_ids), ClaimLink.target.in_(existing_node_ids)))
        existing_gen_links = {(l.source, l.target, l.link_type): l for (l,) in r}
        missing_links = []
        for eid, l in links.items():
            source=all_nodes[l['parent']]
            target=all_nodes[l['child']]
            source_original_type = nodes[l['parent']]['type']
            target_original_type = nodes[l['parent']]['type']
            link_type = convert_link_type(l, source_original_type, target_original_type)
            logger.info(f"Convert: {source_original_type}, {target_original_type}, {l['group']}:{l['form']} => {link_type}")
            if clink := existing_ext_links.get(eid):
                clink.source_fragment = source
                clink.target_fragment = target
                clink.link_type = link_type
            elif clink := existing_gen_links.get((source.id, target.id, link_type)):
                if clink.external_id:
                    logger.error(f"Maybe duplicate link: {l}")
                    continue
                clink.external_id = eid
            else:
                clink = ClaimLink(source_fragment=source, target_fragment=target, external_id=eid, link_type=link_type)
                missing_links.append(clink)
                existing_gen_links[(source.id, target.id, link_type)] = clink
        if missing_links:
            session.add_all(missing_links)
        await session.commit()
    await schedule_fragment_embeds(chain(different_nodes, missing_nodes), collections)
    return chain(different_nodes, missing_nodes)
