"""
ClaimMiner main package.
Set up database access, load ORM models, HashFS access, and a few common utility functions.
"""
# Copyright Society Library and Conversence 2022-2023

import asyncio
import inspect
from contextvars import copy_context
from functools import partial, wraps
from itertools import chain
import os
from pathlib import Path
from configparser import ConfigParser
from typing import Any, Callable, Coroutine

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy import select
from frozendict import frozendict
from hashfs import HashFS

from .models import Analyzer, Collection, DocCollection

config = ConfigParser()
print(Path(__file__).parent.joinpath("config.ini"))
config.read(Path(__file__).parent.parent.joinpath("config.ini"))
production = os.environ.get("PRODUCTION", False)
target_db = os.environ.get("TARGET_DB", "production" if production else "development")
os.environ["OPENAI_API_KEY"] = config.get("openai", "api_key", fallback='')
os.environ["OPENAI_ORGANIZATION"] = config.get("openai", "organization", fallback='')
config.get(target_db, "database")

engine = create_async_engine(
    f"postgresql+asyncpg://{config.get(target_db, 'owner')}:{config.get(target_db, 'owner_password')}@{config.get('postgres', 'host')}:{config.get('postgres', 'port')}/{config.get(target_db, 'database')}"
)
Session = sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

hashfs = HashFS('files', depth=4, width=1, algorithm='sha256')

ANALYZERS = {}
ANALYZERS_BY_NICKNAME = {}


async def get_analyzer_id(name, version, params=None, nickname=None, full=False):
    global ANALYZERS, ANALYZERS_BY_NICKNAME
    analyzer_id = None
    if nickname:
        if (name, nickname) not in ANALYZERS_BY_NICKNAME:
            async with Session() as session:
                aid = await session.scalar(select(Analyzer.id).filter_by(name=name, version=version, nickname=nickname))
                if (aid is None):
                    analyzer = Analyzer(name=name, version=version, nickname=nickname, params=params)
                    session.add(analyzer)
                    await session.commit()
                    aid = analyzer.id
            ANALYZERS_BY_NICKNAME[(name, nickname)] = aid
        analyzer_id = ANALYZERS_BY_NICKNAME[(name, nickname)]
    else:
        params = frozendict(params or {})
        if (name, params) not in ANALYZERS:
            async with Session() as session:
                aid = await session.scalar(select(Analyzer.id).filter_by(name=name, version=version, params=params))
                if (aid is None):
                    analyzer = Analyzer(name=name, version=version, params=params)
                    session.add(analyzer)
                    await session.commit()
                    aid = analyzer.id
            ANALYZERS[(name, params)] = aid
        analyzer_id = ANALYZERS[(name, params)]
    if full and analyzer_id:
        async with Session() as session:
            r = await session.execute(select(Analyzer).filter_by(id=analyzer_id))
            (analyzer,) = r.one()
            return analyzer
    return analyzer_id



def as_bool(value):
    if type(value) == bool:
        return value
    return str(value).lower() in {'true', 'yes', 'on', '1', 'checked'}


# Copied from Quart, so as to not load quart in kafka worker

def run_sync(func: Callable[..., Any]) -> Callable[..., Coroutine[None, None, Any]]:
    """Ensure that the sync function is run within the event loop.
    If the *func* is not a coroutine it will be wrapped such that
    it runs in the default executor (use loop.set_default_executor
    to change). This ensures that synchronous functions do not
    block the event loop.
    """

    @wraps(func)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, copy_context().run, partial(func, *args, **kwargs)
        )
        if inspect.isgenerator(result):
            return run_sync_iterable(result)  # type: ignore
        else:
            return result

    return _wrapper


async def schedule_fragment_embeds(fragment_ids, collections=None, doc_id=None):
    from .kafka import get_channel
    tasks = []
    if doc_id:
        tasks.append(asyncio.create_task(get_channel("embed").send_soon(key=str(doc_id), value=f"D{doc_id}")))
    elif collections is None:
        with Session() as session:
            r = session.execute(select(Collection).join(DocCollection).filter_by(doc_id=doc_id))
            collections = [c for (c,) in r]

    extra_models = set(chain(*(c.params.get('embeddings', ()) for c in (collections or ()))))
    tasks = []
    for fragment_id in fragment_ids:
        tasks.append(asyncio.create_task(get_channel("embed").send_soon(key=str(fragment_id), value=f"F{fragment_id}")))
        tasks.extend(
            asyncio.create_task((get_channel("embed").send_soon(key=str(fragment_id), value=f"F{fragment_id} {model}")))
            for model in extra_models)
    await asyncio.gather(*tasks)
