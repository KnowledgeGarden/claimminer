"""
Initial definition of the Quart app. It is completed in :py:mod:`claim_miner.app_full`.
"""
# Copyright Society Library and Conversence 2022-2023
import logging

import asyncio

from quart import Quart, session as qsession
from quart_auth import current_user, login_required
from quart_session import Session
from quart_jwt_extended import JWTManager

from . import config, production, as_bool
from .kafka import get_channel, get_producer, stop_producer

logger = logging.getLogger("web")

app = Quart("ClaimMiner")
app.config.from_mapping(config["base"])
app.config["TEMPLATES_AUTO_RELOAD"] = not production
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024
app.config["SESSION_TYPE"] = 'memcached'
app.config["SESSION_KEY_PREFIX"] = 'claimminer_'
app.config['JWT_SECRET_KEY'] = config.get("base", "secret_key")
app.config["QUART_AUTH_COOKIE_SECURE"] = as_bool(app.config.get("quart_auth_cookie_secure", production))
app.secret_key = config.get("base", "secret_key")
if production:
    app.config["SESSION_REVERSE_PROXY"] = True

Session(app)
jwt = JWTManager(app)

@app.before_serving
async def startup():
    await get_producer()

@app.after_serving
async def shutdown():
    await stop_producer()

from .auth import requires_permission
