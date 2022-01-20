"""
Copyright Society Library and Conversence 2022-2023
"""
from functools import wraps

from sqlalchemy.sql import cast
from quart import current_app
from quart.globals import request_ctx
from quart_auth import (
    AuthUser, AuthManager, current_user, Unauthorized, QUART_AUTH_USER_ATTRIBUTE
)

from . import Session, config, select, production, as_bool
from .models import (
    User, permission, Collection, CollectionPermissions, CollectionScope,
    Document, DocCollection, Fragment, FragmentCollection, permission)
from .app import app


class QUser(AuthUser):
    def __init__(self, auth_id):
        super().__init__(auth_id)
        self._resolved = not auth_id
        self._email = None
        self._handle = None
        self._permissions = None

    async def _resolve(self):
        if not self._resolved:
            async with Session() as session:
              r = await session.execute(
                  select(User).filter_by(id=self.auth_id))
              (user,) = r.first()
              self._resolved = True
              self._email = user.email
              self._handle = user.handle
              self._permissions = user.permissions

    @property
    async def email(self):
        await self._resolve()
        return self._email

    @property
    async def handle(self):
        await self._resolve()
        return self._handle

    @property
    async def permissions(self):
        await self._resolve()
        return self._permissions

    async def can(self, perm: permission):
        permissions = await self.permissions or []
        return perm in permissions or "admin" in permissions


def requires_permission(perm: permission):
    def requires_permission_decorator(func):
        "A decorator to restrict route access to users with a given permission"

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not await current_user.is_authenticated:
                raise Unauthorized()
            elif not await current_user.can(perm):
                raise Unauthorized()
            else:
                return await current_app.ensure_async(func)(*args, **kwargs)

        return wrapper
    return requires_permission_decorator


def requires_collection_permission(perm: permission):
    def requires_permission_decorator(func):
        "A decorator to restrict route access to users with a given permission"

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not await current_user.is_authenticated:
                raise Unauthorized()
            elif not await current_user.can(perm):
                collection = kwargs.get("collection", None)
                if not collection:
                    raise Unauthorized()
                async with Session() as session:
                    collection_name = collection if isinstance(collection, str) else collection.name
                    q = select(CollectionPermissions.permissions
                        ).join(Collection
                        ).filter(
                            CollectionPermissions.user_id==current_user.auth_id,
                            Collection.name == collection_name)
                    r = await session.execute(q)
                    r = r.first()
                    if r is None or perm not in r[0]:
                        raise Unauthorized()
            return await current_app.ensure_async(func)(*args, **kwargs)

        return wrapper
    return requires_permission_decorator


def may_require_collection_permission(perm: permission):
    def requires_permission_decorator(func):
        """A decorator to restrict route access to users with a given permission."""

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not await current_user.is_authenticated:
                raise Unauthorized()
            elif not await current_user.can(perm):
                collection = kwargs.get("collection", None)
                if not collection:
                    raise Unauthorized()
                async with Session() as session:
                    collection_name = collection if isinstance(collection, str) else collection.name
                    q = select(CollectionPermissions.permissions
                        ).join(Collection
                        ).filter(
                            CollectionPermissions.user_id==current_user.auth_id,
                            Collection.name == collection_name)
                    r = await session.execute(q)
                    r = r.first()
                    if r is None or perm not in r[0]:
                        raise Unauthorized()
            return await current_app.ensure_async(func)(*args, **kwargs)

        return wrapper
    return requires_permission_decorator


async def doc_collection_constraints(query, collection=None, perm='access', include_in_collection=True, include_outside_collection=None):
    if collection:
        include_in_collection = True
        collection_name = collection if isinstance(collection, str) else collection.name
    if include_outside_collection is None:
        include_outside_collection = not collection
    assert include_in_collection or include_outside_collection
    if not collection:
        if await current_user.can(perm):
            return query
        if include_in_collection:
            if include_outside_collection:
                return query.outerjoin(DocCollection, Document.id==DocCollection.doc_id
                    ).outerjoin(CollectionPermissions, (CollectionPermissions.collection_id==DocCollection.collection_id)
                        & (CollectionPermissions.user_id==current_user.auth_id)
                    ).filter((DocCollection.collection_id == None
                        ) | ((CollectionPermissions.permissions != None)
                            & CollectionPermissions.permissions.any(cast(perm, permission))))
            else:
                return query.join(DocCollection, Document.id==DocCollection.doc_id
                    ).join(CollectionPermissions, (CollectionPermissions.collection_id==DocCollection.collection_id)
                        & (CollectionPermissions.user_id==current_user.auth_id)
                    ).filter((CollectionPermissions.permissions != None)
                            & CollectionPermissions.permissions.any(cast(perm, permission)))
        else:
            return query.outerjoin(DocCollection, Document.id==DocCollection.doc_id
                ).filter(DocCollection.collection_id == None)
    else:
        # todo: no access needed for main connection.
        generic_permissions = await current_user.can(perm)
        coll_perm_cond = ((CollectionPermissions.permissions != None)
            & CollectionPermissions.permissions.any(cast(perm, permission)))
        if include_outside_collection:
            query = query.outerjoin(DocCollection, Document.id==DocCollection.doc_id
                ).outerjoin(Collection, Collection.id==DocCollection.collection_id
                ).filter((Collection.name == collection_name) | (Collection.name == None))
            if not generic_permissions:
                query = query.outerjoin(CollectionPermissions, (CollectionPermissions.collection_id==DocCollection.collection_id)
                    & (CollectionPermissions.user_id==current_user.auth_id)
                ).filter((Collection.name == None) | coll_perm_cond)
        else:
            query = query.join(DocCollection, Document.id==DocCollection.doc_id
                ).join(Collection, Collection.id==DocCollection.collection_id
                ).filter(Collection.name == collection_name)
            if not generic_permissions:
                query = query.join(CollectionPermissions, (CollectionPermissions.collection_id==DocCollection.collection_id)
                    & (CollectionPermissions.user_id==current_user.auth_id)
                ).filter(coll_perm_cond)
    return query


async def check_doc_access(doc_id, collection=None, perm='access'):
    if await current_user.can(perm):
        return True
    q = await doc_collection_constraints(select(Document).filter(Document.id == doc_id), collection, perm)
    async with Session() as session:
        r = await session.execute(q)
        r = r.first()
    if r is None:
        raise Unauthorized()
    return True


async def fragment_collection_constraints(query, collection=None, perm='access', include_in_collection=True, include_outside_collection=None):
    if collection:
        include_in_collection = True
        collection_name = collection if isinstance(collection, str) else collection.name
    if include_outside_collection is None:
        include_outside_collection = not collection
    assert include_in_collection or include_outside_collection
    if not collection:
        if await current_user.can(perm):
            return query
        if include_in_collection:
            if include_outside_collection:
                return query.outerjoin(FragmentCollection, Fragment.id==FragmentCollection.fragment_id
                    ).outerjoin(CollectionPermissions, (CollectionPermissions.collection_id==FragmentCollection.collection_id)
                        & (CollectionPermissions.user_id==current_user.auth_id)
                    ).filter((FragmentCollection.collection_id == None
                        ) | ((CollectionPermissions.permissions != None)
                            & CollectionPermissions.permissions.any(cast(perm, permission))))
            else:
                return query.join(FragmentCollection, Fragment.id==FragmentCollection.fragment_id
                    ).join(CollectionPermissions, (CollectionPermissions.collection_id==FragmentCollection.collection_id)
                        & (CollectionPermissions.user_id==current_user.auth_id)
                    ).filter((CollectionPermissions.permissions != None)
                            & CollectionPermissions.permissions.any(cast(perm, permission)))
        else:
            return query.outerjoin(FragmentCollection, Fragment.id==FragmentCollection.fragment_id
                ).filter(FragmentCollection.collection_id == None)
    else:
        # todo: no access needed for main connection.
        generic_permissions = await current_user.can(perm)
        coll_perm_cond = ((CollectionPermissions.permissions != None)
            & CollectionPermissions.permissions.any(cast(perm, permission)))
        if include_outside_collection:
            query = query.outerjoin(FragmentCollection, Fragment.id==FragmentCollection.fragment_id
                ).outerjoin(Collection, Collection.id==FragmentCollection.collection_id
                ).filter((Collection.name == collection_name) | (Collection.name == None))
            if not generic_permissions:
                query = query.outerjoin(CollectionPermissions, (CollectionPermissions.collection_id==FragmentCollection.collection_id)
                    & (CollectionPermissions.user_id==current_user.auth_id)
                ).filter((Collection.name == None) | coll_perm_cond)
        else:
            query = query.join(FragmentCollection, Fragment.id==FragmentCollection.fragment_id
                ).join(Collection, Collection.id==FragmentCollection.collection_id
                ).filter(Collection.name == collection_name)
            if not generic_permissions:
                query = query.join(CollectionPermissions, (CollectionPermissions.collection_id==FragmentCollection.collection_id)
                    & (CollectionPermissions.user_id==current_user.auth_id)
                ).filter(coll_perm_cond)
    return query


async def check_fragment_access(fragment_id, collection=None, perm='access'):
    if await current_user.can(perm):
        return True
    q = fragment_collection_constraints(select(Document), collection, perm).filter(Document.id == fragment_id)
    async with Session() as session:
        r = await session.execute(q)
        r = r.first()
    if r is None:
        raise Unauthorized()
    return True

async def set_user(user_id):
    user = QUser(user_id)
    await user._resolve()
    setattr(request_ctx, QUART_AUTH_USER_ATTRIBUTE, user)
    return user


auth_manager = AuthManager()
auth_manager.user_class = QUser
auth_manager.init_app(app)

