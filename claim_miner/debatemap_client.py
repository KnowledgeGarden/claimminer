"""
Copyright Society Library and Conversence 2022-2023
"""
import backoff
import ssl

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.websockets import WebsocketsTransport
from gql.transport.exceptions import TransportAlreadyConnected

from . import config


timeout = int(config.get('debatemap', 'timeout', fallback=300))
_headers = None

def getHeaders():
    global _headers
    if _headers is None:
        referer = config.get('debatemap', 'graphql_referer', fallback=None)
        token = config.get('debatemap', 'token', fallback=None)
        _headers = {}
        if referer:
            _headers["Referer"] = referer
        if token:
            _headers['Authorization'] = f"Bearer {token}"
        print(_headers)
    return _headers

def getClient():
    endpoint = config.get('debatemap', 'graphql_endpoint')
    transport = AIOHTTPTransport(url=endpoint, headers=getHeaders())
    return Client(
        transport=transport, fetch_schema_from_transport=True, execute_timeout=timeout)

client = getClient()


def getWsTransport():
    endpoint = 'ws' + config.get('debatemap', 'graphql_endpoint')[4:]
    return WebsocketsTransport(url=endpoint, headers=getHeaders(), subprotocols=[WebsocketsTransport.APOLLO_SUBPROTOCOL])


@backoff.on_exception(backoff.expo, TransportAlreadyConnected, max_time=timeout)
async def debatemap_query(query, **kwargs):
    async with client as session:
        return await session.execute(query, variable_values=kwargs)


descendants_query = gql("""
query getNodeIds($nodeId: String!, $depth: Int!) {
  descendants(rootNodeId: $nodeId, maxDepth: $depth) {
    id
  }
}
""")

ancestors_query = gql("""
query getNodeIds($nodeId: String!, $depth: Int!) {
  ancestors_query(rootNodeId: $nodeId, maxDepth: $depth) {
    id
  }
}
""")

path_query = gql("""
query getPath($startNode: String!, $endNode: String!) {
  shortestPath(startNode: $startNode, endNode: $endNode) {
    nodeId
  }
}
""")

policy_subscription = gql("""subscription {
  accessPolicies(filter: {}) {
    nodes {
      id
      name
    }
  }
}
""")

access_policies = None
async def getAccessPolicies():
    global access_policies
    if access_policies is None:
        async with Client(transport=getWsTransport(), fetch_schema_from_transport=False) as client:
            async for response in client.subscribe(policy_subscription):
                access_policies = {n['id']: n['name'] for n in response['accessPolicies']['nodes']}
                break
    return access_policies


node_data_query = gql("""
query getNodeData($nodeId: String!, $depth: Int!) {
  subtree(rootNodeId: $nodeId, maxDepth: $depth) {
    nodes {
      id
      type
      c_currentRevision
      multiPremiseArgument
    }
    nodeRevisions {
      id
      phrasing {
        text_question
        text_base
      }
      attachments {
        references
        quote
      }
    }
    nodePhrasings {
      node
      type
      text_base
      text_question
    }
    nodeLinks {
      id
      parent
      child
      group
      polarity
      form
    }
  }
}
""")


node_type_data = {
  'standalone': "claim",
  'standalone_category': "category",
  'standalone_question': "multiChoiceQuestion",
  'standalone_claim': "claim",
  'standalone_argument': "argument",
  'reified_arg_link': "argument",
  # Not sure how to use package
}

node_type_data_rev = {
  "category": 'standalone_category',
  "multiChoiceQuestion": 'standalone_question',
  "claim": 'standalone_claim',
  "argument": 'standalone_argument',
  "package": 'standalone_category'
}

def convert_node_type(node_type, text, multiPremiseArgument):
    if node_type == 'argument' and not text:
        return 'reified_arg_link'
    return node_type_data_rev[node_type]


def convert_link_type(link, parent_type, child_type):
    # logger.debug(f'convert_link_type {parent_type} --({link['group']})--> {child_type}')
    if link['group'] == 'truth':
        return 'supported_by' if link['polarity'] == 'supporting' else 'opposed_by'
    if link['group'] == 'relevance':
        return 'relevant' if link['polarity'] == 'supporting' else 'irrelevant'
    if child_type == 'category':
        return 'subcategory'
    if child_type == 'multiChoiceQuestion':
        return 'subquestion'
    if parent_type == 'category':
        if child_type == 'claim':
            return 'subquestion' if link['form'] == 'question' else 'subclaim'
        # Not sure what to do otherwise
    if link['group'] == 'freeform':
        if child_type == 'argument':
            return 'supported_by' if link['polarity'] == 'supporting' else 'opposed_by'
        # again unsure
        return 'freeform'
    if link['group'] == 'generic':
        if (parent_type, child_type) == ('argument', 'claim'):
            return 'subquestion' if link['form'] == 'question' else 'has_premise'
        if (parent_type, child_type) == ('multiChoiceQuestion', 'claim'):
            return 'subquestion' if link['form'] != 'question' else 'answers_question'
        # Again unsure
    # not treating group == neutrality
    return 'freeform'


link_type_data = {
    'freeform': (dict(group="freeform", form=None, polarity=None),),
    'answers_question': (dict(group="generic", form="base", polarity=None),),
    'has_premise': (dict(group="generic", form=None, polarity=None),),
    'irrelevant': (dict(group="relevance", form=None, polarity="opposing"),),
    'opposed_by': (dict(group="truth", form=None, polarity="opposing"), dict(group="generic", form="base", polarity=None)),
    'relevant': (dict(group="relevance", form=None, polarity="supporting"),),
    'subcategory': (dict(group="generic", form=None, polarity=None),),
    'subclaim': (dict(group="generic", form="base", polarity=None),),
    'subquestion': (dict(group="generic", form='question', polarity=None),),
    'supported_by': (dict(group="truth", form=None, polarity="supporting"), dict(group="generic", form="base", polarity=None)),
    # 'implied': (dict(),),
    # 'implicit': (dict(),),
    # 'derived': (dict(),),
}


# types of nodes:  {'argument', 'category', 'claim', 'multiChoiceQuestion', 'package'}
# argumentType of nodes: {'any', 'anyTwo', 'all'}
# forms of nodeLinks: {None, 'base', 'question', 'negation'}
# polarity of nodeLinks: {None, 'opposing', 'supporting'}
# group of nodeLinks: {'generic', 'relevance', 'truth'}


add_child_node_mutation = gql("""
mutation callAddChildNode($input: AddChildNodeInput!) {
  addChildNode(input: $input) {
    nodeID
    linkID
    revisionID
  }
}
""")

add_argument_mutation = gql("""
mutation callAddArgumentAndClaimInput($input: AddArgumentAndClaimInput!) {
  addArgumentAndClaim(input: $input) {
    argumentNodeID
    argumentRevisionID
    claimNodeID
    claimRevisionID
  }
}
""")

def make_debatemap_sources(source):
    sources = [{
        "type": "claimMiner",
        "claimMinerId": f'urn:x-claimminer:{source.doc_id}#{source.id}'}]
    document = source.document
    if not document:
        return sources
    source = {}
    if document.url.startswith('http'):
        sources.append({"type": "webpage", "link": source.document.url})
    if document.url.startswith('urn'):
        source['urn'] = document.url
    elif urns:= [uri for uri in document.uri.equivalents if uri.status == 'urn']:
        source['urn'] = urns[0].uri
    if authors := document.meta.get('authors'):
        source['author'] = authors
    if document.title:
        source['title'] = document.title
    if source:
        source['type'] = 'text'
        sources.append(source)
    return sources


async def add_child_node(
        parentId,
        text,
        mapId,
        accessPolicy,
        my_node_type,
        my_link_type,
        orderKey="a0",
        sources=None,
    ):
    node_type = node_type_data[my_node_type]
    link_data = link_type_data[my_link_type]
    if sources:
        # TODO: Type depends on URL.
        attachments = [{
              "quote": {
                "content": source.text,
                "sourceChains": [
                  {"sources": make_debatemap_sources(source)},
                ]
              }
            } for source in sources]
    else:
        attachments = []

    if len(link_data) == 1:
        link_data = link_data[0]
        result = await debatemap_query(add_child_node_mutation, input={
            "mapID": mapId,
            "parentID": parentId,
            "node": {
                "accessPolicy": accessPolicy,
                "type": node_type,
            },
            "revision": {
                "phrasing": {
                    "text_base": text,
                    "terms": []
                  },
                "attachments": attachments
              },
            "link": {
                "group": link_data['group'],
                "form": link_data['form'],
                "polarity": link_data['polarity'],
                "orderKey": orderKey
            }
        })
    else:
        argLinkData, claimLinkData = link_data
        result = await debatemap_query(add_argument_mutation, input={
            "mapID": mapId,
            "argumentParentID": parentId,
            "argumentNode": {
              "accessPolicy": accessPolicy,
              "type": "argument"
            },
            "argumentRevision": {
              "phrasing": {
                "text_base": "",
                "terms": []
              },
              "attachments": []
            },
            "argumentLink": {
              "group": argLinkData['group'],
              "form": argLinkData['form'],
              "polarity": argLinkData['polarity'],
              "orderKey": orderKey
            },
            "claimNode": {
              "accessPolicy": accessPolicy,
              "type": node_type
            },
            "claimRevision": {
              "phrasing": {
                "text_base": text,
                "terms": []
              },
              "attachments": attachments
            },
            "claimLink": {
              "group": claimLinkData['group'],
              "form": claimLinkData['form'],
              "orderKey": "a0"
            }
        })
    return result['addChildNode']


async def export_node(
      session,
      node,
      collection,
      link=None,
      parent=None,
   ):
    parent_id = parent.external_id if parent else collection.params['debatemap_node']
    map_id = collection.params['debatemap_map']
    policy_id = collection.params['debatemap_policy']
    if link is None:
        link_type = 'freeform'
    elif isinstance(link, str):
        link_type = link
        link = None
    elif isinstance(link, object):
        link_type = link.link_type
    else:
        link_type = 'freeform'
        link = None
    sources = await node.load_sources(session)
    for source in sources:
        await session.refresh(source.document.uri, ['equivalents'])

    results = await add_child_node(
        parent_id,
        node.text,
        map_id,
        policy_id,
        node.scale,
        link_type,
        sources=sources
    )
    if 'nodeID' in results:
        node.external_id = results['nodeID']
        if link:
            link.external_id = results['linkID']
        else:
            pass  # will be picked up by sync...
    else:
        node.external_id = results['claimNodeID']
        # let the rest come from sync as well?
