"""
Completing the definition of the Quart app in :py:mod:`claim_miner.app`, loading all the routes.
"""
# Copyright Society Library and Conversence 2022-2023
import logging

from . import config
from .app import app

if "web_logging" in config:
    logging.basicConfig(**dict(config.items("web_logging", {})))

from .web import *

# @app.websocket("/ws")
# @login_required
# async def ws():
#      await websocket.send(f"Hello {current_user.auth_id}")

if __name__ == "__main__":
    port = int(config.get("base", "port", fallback=5000))
    app.run(port=port)
