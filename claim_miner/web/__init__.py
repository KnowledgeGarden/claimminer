"""
In this package, the web application's routes are implemented.
"""
# Copyright Society Library and Conversence 2022-2023
from sqlalchemy import select

from ..app import logger, qsession, Session, app
from ..models import CollectionScope, visible_standalone_type_names, standalone_type_names, link_type_names
from .. import schedule_fragment_embeds

app.jinja_env.globals.update(dict(
    visible_standalone_type_names=visible_standalone_type_names,
    standalone_type_names=standalone_type_names,
    link_type_names=link_type_names,
))

def overlaps(f1, f2):
    return max(f1.char_position, f2.char_position) < min(f1.char_position + len(f1.text), f2.char_position+len(f2.text))


def render_with_spans(text, fragments):
    if not fragments:
        return text
    use_fragments = fragments[:]
    use_fragments.sort(key=lambda x: (x[1].char_position, len(x[1].text)))
    # eliminate overlaps
    previous = None
    fragments = []
    for fragment in use_fragments:
        if previous and overlaps(previous[1], fragment[1]):
            continue
        fragments.append(fragment)
        previous = fragment
    position = 0
    render = ''
    for fragment in fragments:
        fstart = fragment.char_position
        if fstart > position:
            render += text[position:fstart]
            position = fstart
        render += '<span class="boundary">'
        render += fragment.text
        render += '</span>'
        position += len(fragment.text)
    render += text[position:]
    return f'<span>{render}</span>'


def update_fragment_selection(selection_changes=None, reset_fragments=False):
    selection_changes = json.loads(selection_changes or "{}")
    if reset_fragments:
        selection = set()
    else:
        selection = set(qsession.get('fragments_selection', ()))
    for k, v in selection_changes.items():
        k = int(k)
        if v:
            selection.add(k)
        else:
            selection.discard(k)
    if selection_changes or reset_fragments:
        qsession['fragments_selection'] = list(selection)
    return selection

collection_path = CollectionScope.collection_path
get_collection = CollectionScope.get_collection
get_collections_and_scope = CollectionScope.get_collections_and_scope

async def get_base_template_vars(current_user, collection=None, session=None):
    if session is None:
        async with Session() as session:
            return await get_base_template_vars(current_user, collection, session)

    collection = await get_collection(collection, session, current_user.auth_id)
    collection_names = await CollectionScope.get_collection_names(session)

    def user_can(permission):
        return collection.user_can(current_user, permission)
    return dict(collection=collection, collection_names=collection_names, user_can=user_can)



from .auth_routes import *
from .docs import *
from .search import *
from .dashboard import *
from .claim import *
from .claim_clusters import *
from .claim_index import *
from .scatterplot import *
from .cse import *
from .collection import *
from .prompts import *
