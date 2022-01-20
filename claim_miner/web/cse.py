"""
Copyright Society Library and Conversence 2022-2023
"""
from pathlib import Path
from google.auth import load_credentials_from_file
from googleapiclient.discovery import build
from quart import request
from quart_cors import route_cors
from .. import config
from ..app import app, logger

cse = None
cx = config.get("cse", "cx", fallback=None)
credential_filename = config.get("cse", "google_credentials", fallback=None)
if cx and credential_filename:
    credentials, project_id = load_credentials_from_file(
        Path(__file__).parent.parent.parent.joinpath(credential_filename)
    )
    scredentials = credentials.with_scopes(['https://www.googleapis.com/auth/cse'])
    service = build('customsearch', 'v1', credentials=scredentials)
    cse = service.cse()

@app.route("/cse_proxy", methods=["GET"])
@route_cors(allow_origin='*')
async def cse_proxy():
    q = request.args.get('q')
    offset = request.args.get('offset', type=int, default=1)
    # limit is always 10.
    logger.debug("cse: %s", dict(q=q, start=offset, cx=cx))
    req = cse.list(cx=cx, q=q, start=offset)
    logger.debug(req)
    return req.execute()
