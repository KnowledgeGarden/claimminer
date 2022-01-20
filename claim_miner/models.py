"""
The SQLAlchemy ORM models for ClaimMiner
"""
# Copyright Society Library and Conversence 2022-2023
from collections import defaultdict

from sqlalchemy import Table, Column, Integer, String, DateTime, Boolean, ForeignKey, Float, Text, case, literal_column, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, ENUM
from sqlalchemy.orm import declarative_base, relationship, declared_attr, joinedload, backref, subqueryload
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import cast
from sqlalchemy.sql.functions import coalesce, func
from sqlalchemy.sql.type_api import TypeEngine
from sqlalchemy.dialects.postgresql.base import PGTypeCompiler
from pgvector.sqlalchemy import Vector

class regconfig(TypeEngine):

    """Provide the PostgreSQL regconfig type.
    """

    __visit_name__ = "regconfig"


PGTypeCompiler.visit_regconfig = lambda self, type_, **kw: "regconfig"

def as_regconfig(lang):
    return cast(lang, regconfig)

en_regconfig = as_regconfig('english')


Base = declarative_base()
"""Declarative base class"""

permission = ENUM('admin',
    'access',  #: Read access to the collection's data
    'add_document',  #: Add a document to the collection
    'add_claim',  #: Add a claim to the collection
    'claim_score_query',  # Call to scoring service
    'bigdata_query',  #: Make a BigData query against GDELT
    'openai_query',  #: Make a query against OpenAI api
    'confirm_claim',  # You can set a claim to non-draft state
    'edit_prompts',  # You can edit prompts
    name='permission')
"""The enum of permissions that a user can have, related to specific tasks"""

# Unused
process_status = ENUM(
    'pending',
    'ongoing',
    'complete',
    'error',
    name='process_status')

standalone_type_names = {
  'reified_arg_link': "Empty Argument",  #: Argument wrapper
  'standalone': "Generic",  #: standalone statement of unspecified subtype
  'generated': "Generated",  #: Used for boundaries found by claim analyzers. To be removed.
  'standalone_root': "Import root",  #: Connected to a DebateMap root claim, for importation.
  'standalone_category': "Category",  #: generic grouping category
  'standalone_question': "Question",  #: A multi-answer question
  'standalone_claim': "Claim",  #: A claim (also represents its associated yes-no question in DebateMap)
  'standalone_argument': "Argument",  #: An argument that a (set of) Claims makes another Claim more or less plausible. Reified connection.
}
# Fragment types for standalone fragments (not part of a document) and their user-readable names.

standalone_types = list(standalone_type_names)

visible_standalone_types = [
  'standalone',
  'standalone_category',
  'standalone_question',
  'standalone_claim',
  'standalone_argument',
]
"""The subset of standalone fragment types that can be chosen in prompt construction, and that are displayed to the user."""

visible_standalone_type_names = {k: v for (k, v) in standalone_type_names.items() if k in visible_standalone_types}

fragment_type = ENUM(
  'document',  #: represents the document as a whole. Not used.
  'paragraph',  #: represents a paragraph in the document.
  'sentence',  #: Represents a sentence in a paragraph. Not used yet
  'phrase',  # Represents a phrase in a sentence. Not used yet
  'quote',  # Represents a quote from a document. May span paragraphs. Not used yet.
  *standalone_types,
  name='fragment_type')
"""The set of all fragment subtypes"""

link_type = ENUM(
    'freeform',
    'key_point',
    'supported_by',
    'opposed_by',
    'implied',
    'implicit',
    'derived',
    'answers_question',
    'has_premise',
    'irrelevant',
    'relevant',
    'subcategory',
    'subclaim',
    'subquestion',
    'quote',
    name='link_type'
)
"""The set of all types of link between claims"""


link_type_names = {
    'freeform': "Freeform",
    'supported_by': "Supported by",
    'opposed_by': "Opposed by",
    'answers_question': "Answers question",
    'has_premise': "Has premise",
    'irrelevant': "Irrelevant",
    'relevant': "Relevant",
    'subcategory': "Sub-category",
    'subclaim': "Sub-claim",
    'subquestion': "Sub-question",
}
"""User-readable names for link types"""


uri_status = ENUM(
    'canonical',  #: Canonical member of the equivalence class
    'urn',  #: Distinguished non-URL member of the equivalence class
    'snapshot',  #: Reference to a snapshot URL, eg archive.org
    'alt',  #: Alternate (non-canonical) URL/URI with the same content
    'unknown',  #: Undetermined canonicality
    name='uri_status'
)
"""Status of URIs in an equivalence class"""

class User(Base):
    """ClaimMiner users."""
    __tablename__ = 'user'

    id = Column(Integer, primary_key=True)  #: Primary key
    handle = Column(String)  #: username
    passwd = Column(String)  #: password (scram hash)
    email = Column(String)  #: email
    confirmed = Column(Boolean, server_default='false')  #: account confirmed by admin
    created = Column(DateTime, server_default='now()')  #: date of creation
    permissions = Column(ARRAY(permission))  #: User's global permissions

    def can(self, permission):
        "Does the user have this permission?"
        permissions = self.permissions or []
        return (permission in permissions) or ("admin" in permissions)

class Analyzer(Base):
    """A versioned computation process.
    Computed values keep a reference to the analyzer that created them.
    The versioning system is not being used yet.
    """
    __tablename__ = 'analyzer'
    id = Column(Integer, primary_key=True)  #: Primary key
    name = Column(String)  #: the type of analyzer
    nickname = Column(String)  #: User-readable subtype, used for prompt names
    version = Column(Integer)  #: the version number
    params = Column(JSONB, server_default='{}')  #: Prompt logic is here
    draft = Column(Boolean, server_default='false')
    """True while editing a prompt, false when it has been used.
    Avoid editing an analyzer that is tied to an existing analysis."""

class DocCollection(Base):
    """Join table between Document and Collection"""
    __tablename__ = 'doc_collection'
    doc_id = Column(Integer, ForeignKey('document.id'), primary_key=True)
    collection_id = Column(Integer, ForeignKey('collection.id'), primary_key=True)

class FragmentCollection(Base):
    """Join table between Fragment and Collection"""
    __tablename__ = 'fragment_collection'
    fragment_id = Column(Integer, ForeignKey('fragment.id'), primary_key=True)
    collection_id = Column(Integer, ForeignKey('collection.id'), primary_key=True)

class CollectionScope():
    """An abstract class defining default behaviour for Collections"""
    @property
    def path(self):
        return ''

    async def user_can(self, user, permission):
        return await user.can(permission)

    def embed_model(self):
        return BASE_EMBED_MODEL

    @staticmethod
    async def get_collection(name, session=None, user_id=None):
        if not name:
            return globalScope
        if isinstance(name, CollectionScope):
            return name
        if not session:
            from . import Session
            async with Session() as session:
                return await CollectionScope.get_collection(name, session, user_id)
        q = select(Collection).filter_by(name=name).limit(1)
        if user_id:
            q = q.outerjoin(
                CollectionPermissions,
                (CollectionPermissions.collection_id == Collection.id) &
                (CollectionPermissions.user_id == user_id)
                ).add_columns(CollectionPermissions)
        r = await session.execute(q)
        r = r.first()
        if not r:
            raise ValueError("Unknown collection: ", name)
        collection = r[0]
        if user_id:
            collection.user_permissions = r[1]
        return collection

    @staticmethod
    async def get_collections_and_scope(collections, session=None, user_id=None):
        clist = []
        if isinstance(collections, list):
            for coll in collections:
                clist.append(await CollectionScope.get_collection(coll, session, user_id))
            scope = collections[0]
        else:
            scope = await CollectionScope.get_collection(collections, session, user_id)
            if scope:
                clist.append(scope)
        return clist, scope

    @staticmethod
    def collection_path(name):
        return f'/c/{name}' if name else ''

    @staticmethod
    async def get_collection_names(session):
        r = await session.execute(select(Collection.name))
        return [n for (n,) in r]


class Collection(Base, CollectionScope):
    """A named collection of Documents and Claims"""
    __tablename__ = 'collection'
    id = Column(Integer, primary_key=True)  #: Primary key
    name = Column(String, nullable=False)  #: name (should be a slug)
    params = Column(JSONB, server_default='{}')  #: Supplemental information
    documents = relationship('Document', secondary=DocCollection.__table__, back_populates='collections')
    "The documents in the collection"
    fragments = relationship('Fragment', secondary=FragmentCollection.__table__, back_populates='collections')
    "The fragments in the collection"
    permissions = relationship('CollectionPermissions', back_populates='collection')
    "Collection-specific permissions"

    @property
    def path(self):
        return CollectionScope.collection_path(self.name)

    def embed_model(self):
        embeddings = self.params.get("embeddings", [])
        for embedding in embeddings:
            if embedding == BASE_EMBED_MODEL:
                continue
            return embedding
        return BASE_EMBED_MODEL

    async def user_can(self, user, permission):
        if await super().user_can(user, permission):
            return True
        if cp := getattr(self, 'user_permissions', None):
            permissions = cp.permissions or []
            return (permission in permissions) or ("admin" in permissions)


class GlobalScope(CollectionScope):
    """The global scope: All documents and fragments, belonging to any collection."""
    params = {}
    def __bool__(self):
        return False


globalScope = GlobalScope()

class CollectionPermissions(Base):
    """Collection-specific permissions that a user has in the scope of a specific collection"""
    __tablename__ = 'collection_permissions'
    user_id = Column(Integer, ForeignKey(User.id), primary_key=True)
    collection_id = Column(Integer, ForeignKey(Collection.id), primary_key=True)
    permissions = Column(ARRAY(permission))

    collection = relationship(Collection, back_populates='permissions')
    user = relationship(User)


class UriEquiv(Base):
    """Equivalence classes of URIs"""
    __tablename__ = 'uri_equiv'
    id = Column(Integer, primary_key=True)  #: Primary key
    status = Column(uri_status, nullable=False, server_default='unknown')
    canonical_id = Column(Integer, ForeignKey('uri_equiv.id'))
    uri = Column(String, nullable=False, unique=True)
    canonical = relationship("UriEquiv", remote_side=[id], backref=backref("equivalents"))


class Document(Base):
    """Represents a document that was requested, uploaded or downloaded"""
    __tablename__ = 'document'
    id = Column(Integer, primary_key=True)  #: Primary key
    uri_id = Column(Integer, ForeignKey(UriEquiv.id), nullable=False)  #: Reference to URI, unique for non-archive
    is_archive = Column(Boolean, nullable=False, server_default='false')  #: For multiple snapshots of same document
    requested = Column(DateTime(True), nullable=False, server_default='now()')  #: When was the document requested
    return_code = Column(Integer)  #: What was the return code when asking for the document
    retrieved = Column(DateTime)  #: When was the document retrieved
    created = Column(DateTime)  #: When was the document created (according to HTTP headers)
    modified = Column(DateTime)  #: When was the document last modified (according to HTTP headers)
    mimetype = Column(String)  #: MIME type (according to HTTP headers)
    language = Column(String)    #: Document language (according to HTTP headers, and langdetect)
    added_by = Column(Integer, ForeignKey('user.id'))  #: Who asked for this document to be added
    text_analyzer_id = Column(Integer, ForeignKey('analyzer.id'))  #: What analyzer extracted the text from this document if any
    etag = Column(String)  #: Document etag (according to HTTP headers)
    file_identity = Column(String)  #: Hash of document content, refers to HashFS
    file_size = Column(Integer)  #: Document size (in bytes)
    text_identity = Column(String)  #: Hash of text extracted from document, refers to HashFS
    text_size = Column(Integer)  # Text length (in bytes)
    title = Column(Text)  # Title extracted from the document (HTML or PDF...)
    process_params = Column(JSONB)  # Paramaters that were used to extract and segment the text
    meta = Column('metadata', JSONB, server_default='{}')  #: Metadata column
    public_contents = Column(Boolean, nullable=False, server_default='true')  # Whether the contents are protected by copyright
    collections = relationship(Collection, secondary=DocCollection.__table__, back_populates='documents', overlaps='collection')
    "The collections this document belongs to"
    uri = relationship(UriEquiv, lazy='joined', innerjoin=True) #: The canonical URI of this document

    @property
    def url(self):
        return self.uri.uri

    @hybrid_property
    def load_status(self):
        return case({literal_column("200"): literal_column("'loaded'"), literal_column("0"): literal_column("'not_loaded'")},
                    value=coalesce(self.return_code, literal_column("0")) , else_=literal_column("'error'")).label("load_status")


analysis_context_table = Table(
    "analysis_context",
    Base.metadata,
    Column("analysis_id", ForeignKey("analysis.id")),
    Column("fragment_id", ForeignKey("fragment.id")),
)


class Fragment(Base):
    """A fragment of text. Can be part of a document, or even part of another fragment. It can be a standalone claim.
    """
    __tablename__ = 'fragment'
    id = Column(Integer, primary_key=True)  #: Primary key
    doc_id = Column(Integer, ForeignKey('document.id'))  #: Which document is this fragment part of (if any)
    position = Column(Integer, nullable=False)  #: What is the relative position in the sequence of paragraphs
    char_position = Column(Integer, nullable=False)  #: What is the character start position of this paragraph in the text
    text = Column(Text, nullable=False)  #: What is the text content of the document
    scale = Column(fragment_type, nullable=False)  #: What type of fragment?
    language = Column(String, nullable=False)  # What is the language of the fragment? Inferred from document language or langdetect.
    created_by = Column(Integer, ForeignKey('user.id'))  # Who created the fragment?
    part_of = Column(Integer, ForeignKey('fragment.id'))  # Is this part of another fragment? (E.g. a sentence or quote in a paragraph)
    external_id = Column(String)  #: Used for DebateMap Id when synchronizing claims
    analysis_id = Column(Integer, ForeignKey('analysis.id'))  #: Which analysis generated this fragment (eg segmenter, prompts)
    generation_data = Column(JSONB)  #: Data indicating the generation process
    confirmed = Column(Boolean, nullable=False, server_default="true")  # Confirmed vs Draft

    part_of_fragment = relationship('Fragment', foreign_keys=[part_of])
    collections = relationship(Collection, secondary=FragmentCollection.__table__, back_populates='fragments', overlaps='collection')
    document = relationship('Document')
    from_analysis = relationship('Analysis', foreign_keys=[analysis_id], back_populates='generated_fragments')
    context_of_analyses = relationship('Analysis', secondary=analysis_context_table, back_populates='context')

    @classmethod
    def ptmatch(cls, language=None):
        "For text search"
        vect = func.to_tsvector(as_regconfig(language), cls.text) if language else func.to_tsvector(cls.text)
        return vect.op("@@", return_type=Boolean)

    @hybrid_property
    def is_claim(self):
        "Is the fragment a standalone claim?"
        return fragment_type.enums.index(self.scale) >= fragment_type.enums.index('reified_arg_link')

    @is_claim.inplace.expression
    @classmethod
    def _is_claim_expression(cls):
        return cls.scale >= 'reified_arg_link'

    @hybrid_property
    def is_visible_claim(self):
        """Is the fragment a user-visible standalone claim?
        Excludes quotes generated by claim analyzers, synchronization roots, etc."""
        return self.scale in visible_standalone_types

    @is_visible_claim.inplace.expression
    @classmethod
    def _is_visible_claim_expression(cls):
        return cls.scale.in_(visible_standalone_types)

    async def load_sources(self, session):
        if source_ids := (self.generation_data or {}).get("sources"):
            sources = await session.execute(
                select(Fragment
                    ).filter(Fragment.id.in_(source_ids)
                    ).options(joinedload(Fragment.document)))
            return [s for (s,) in sources]
        return []

    def paths_to(self, fragment):
        for link in self.outgoing_links:
            if link.target_fragment == fragment:
                yield link

    def paths_from(self, fragment):
        for link in self.incoming_links:
            if link.source_fragment == fragment:
                yield link

class Analysis(Base):
    __tablename__ = 'analysis'

    id = Column(Integer, primary_key=True)
    analyzer_id = Column(Integer, ForeignKey(Analyzer.id), nullable=False)
    theme_id = Column(Integer, ForeignKey(Fragment.id))
    params = Column(JSONB, server_default='{}')
    results = Column(JSONB, nullable=False)
    created = Column(DateTime, server_default='now()', nullable=False)

    analyzer = relationship(Analyzer)
    theme = relationship(Fragment, foreign_keys=[theme_id], back_populates='theme_of_analyses')
    context = relationship(Fragment, secondary=analysis_context_table, back_populates='context_of_analyses')
    generated_fragments = relationship(Fragment, foreign_keys=[Fragment.analysis_id], back_populates='from_analysis')


Fragment.theme_of_analyses = relationship(Analysis, foreign_keys=[Analysis.theme_id], back_populates='theme')
DocCollection.collection = relationship(Collection, viewonly=True)
DocCollection.document = relationship(Document, viewonly=True)
FragmentCollection.collection = relationship(Collection, viewonly=True)
FragmentCollection.fragment = relationship(Fragment, viewonly=True)

class Embedding():
    """The vector embedding of a fragment's text. Abstract class."""
    scale = Column(fragment_type, nullable=False)

    @declared_attr
    def analyzer_id(cls):
        return Column(Integer, ForeignKey('analyzer.id'), nullable=False)

    @declared_attr
    def doc_id(cls):
        return Column(Integer, ForeignKey('document.id'), primary_key=True, nullable=True)

    @declared_attr
    def fragment_id(cls):
        return Column(Integer, ForeignKey('fragment.id'), primary_key=True, nullable=True)

    @declared_attr
    def document(cls):
        return relationship(Document,
            primaryjoin=(cls.doc_id==Document.id) & (cls.fragment_id==None))

    @declared_attr
    def fragment(cls):
        return relationship(Fragment,
            primaryjoin=(cls.fragment_id==Fragment.id))

    @declared_attr
    def embedding(cls):
        return Column(Vector(cls.dimensionality), nullable=False)

    @classmethod
    def distance(cls):
        return cls.embedding.cosine_distance


BASE_EMBED_MODEL = 'universal_sentence_encoder_4'
OPENAI_EMBED_MODEL = 'txt_embed_ada_2'

model_names = [
    BASE_EMBED_MODEL,
    OPENAI_EMBED_MODEL,
]


class Embedding_Use4(Embedding, Base):
    """A table for embeddings using Google's Universal sentence encoder 4"""
    __tablename__ = 'embedding_use4'
    model_name = BASE_EMBED_MODEL
    dimensionality = 512

class Embedding_Ada2(Embedding, Base):
    """A table for OpenAI's Ada 2 text embeddings"""
    __tablename__ = 'embedding_ada2'
    model_name = OPENAI_EMBED_MODEL
    dimensionality = 1536


embed_models = {cls.model_name: cls for cls in (Embedding_Ada2, Embedding_Use4)}


class ClaimLink(Base):
    """A typed link between two standalone claims."""
    __tablename__ = 'claim_link'
    source = Column(Integer, ForeignKey('fragment.id'), primary_key=True, nullable=False)
    target = Column(Integer, ForeignKey('fragment.id'), primary_key=True, nullable=False)
    link_type = Column(link_type, primary_key=True, nullable=False)
    analyzer = Column(Integer, ForeignKey('analyzer.id'))
    created_by = Column(Integer, ForeignKey('user.id'))
    score = Column(Float)
    external_id = Column(String)

    source_fragment = relationship(Fragment, foreign_keys=[source], backref="outgoing_links")
    target_fragment = relationship(Fragment, foreign_keys=[target], backref="incoming_links")


async def claim_neighbourhood(nid: int, session):
    children = select(
        ClaimLink.target.label('id'), Fragment.scale, literal_column("'child'").label('level')
        ).join(ClaimLink.target_fragment
        ).filter(ClaimLink.source==nid).cte('children')
    grandchildren = select(
        ClaimLink.target.label('id'), Fragment.scale, literal_column("'grandchild'").label('level')
        ).join(ClaimLink.target_fragment
        ).join(children, (ClaimLink.source==children.c.id) & (children.c.scale=='reified_arg_link')).cte('grandchildren')
    parents = select(
        ClaimLink.source.label('id'), Fragment.scale, literal_column("'parent'").label('level')
        ).join(ClaimLink.source_fragment
        ).filter(ClaimLink.target==nid).cte('parents')
    grandparents = select(
        ClaimLink.source.label('id'), Fragment.scale, literal_column("'grandparent'").label('level')
        ).join(ClaimLink.source_fragment
        ).join(parents, (ClaimLink.target==parents.c.id) & (parents.c.scale=='reified_arg_link')).cte('grandparents')
    all_ids = select(
        literal_column(str(nid), Integer).label('id'), literal_column("'self'").label('level')
        ).union_all(
            select(children.c.id, children.c.level),
            select(grandchildren.c.id, grandchildren.c.level),
            select(parents.c.id, parents.c.level),
            select(grandparents.c.id, grandparents.c.level)).cte('all_ids')
    nodes = await session.execute(
        select(Fragment, all_ids.c.level).join(all_ids, all_ids.c.id==Fragment.id).order_by(all_ids.c.level)
        .options(subqueryload(Fragment.outgoing_links), subqueryload(Fragment.incoming_links))
    )
    target = [n for n, l in nodes if l == 'self'][0]

    def get_paths(direction: bool):
        for link in (target.outgoing_links if direction else target.incoming_links):
            direct_node = link.target_fragment if direction else link.source_fragment
            if direct_node.scale != 'reified_arg_link':
                yield (direct_node, link)
            else:
                for l2 in (direct_node.outgoing_links if direction else direct_node.incoming_links):
                    indirect_node = l2.target_fragment if direction else l2.source_fragment
                    yield (indirect_node, l2, direct_node, link)

    return dict(node=target, children=list(get_paths(True)), parents=list(get_paths(False)))
