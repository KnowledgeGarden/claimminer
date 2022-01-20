"""
Copyright Society Library and Conversence 2022-2023
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path

import httpx
from pytz import utc
from sqlalchemy import delete
from sqlalchemy.future import select

from .. import get_analyzer_id, Session, hashfs, uri_equivalence
from ..models import Document, Fragment
from ..kafka import get_channel
from . import logger

def parse_date(date):
    return parsedate_to_datetime(date).astimezone(utc).replace(tzinfo=None)


version = 1

async def do_download(doc_id):
    analyzer_id = await get_analyzer_id("download", version)
    new_data = False
    async with Session() as session:
        r = await session.execute(
            select(Document).filter_by(id=doc_id))
        doc = r.first()
        if not doc:
            logger.error(f"Missing document %d", doc_id)
            return None
        doc = doc[0]
        if doc.file_identity:
            logger.warning("Already downloaded %d", doc_id)
            return None
            # If you want to redownload, delete the file_identity and the fragments
        async with httpx.AsyncClient() as client:
            r = await client.get(doc.url, follow_redirects=True)
        doc.return_code = r.status_code
        if r.status_code == 200:
            doc.retrieved = datetime.now(timezone.utc)
            if last_modified := r.headers.get('Last-Modified', None):
                doc.last_modified = parse_date(last_modified)
            doc.mimetype = r.headers.get('Content-Type', 'text/html')
            doc.language = r.headers.get('Content-Language', "en")
            doc.etag = r.headers.get('ETag', None)
            # no point streaming because hashfs is not async
            address = hashfs.put(BytesIO(r.content))
            new_data = doc.file_identity != address.id
            if new_data:
                if doc.file_identity:
                    hashfs.delete(doc.file_identity)
                    hashfs.delete(doc.text_identity)
                    doc.text_identity = None
                    await session.execute(delete(Fragment).where(doc_id=doc_id))
                doc.file_identity = address.id
                doc.file_size = Path(address.abspath).stat().st_size
                if doc.file_size > 1000:
                    # Don't play equivalence with stubs
                    r = await session.execute(
                        select(Document.uri).filter(
                            Document.file_identity==address.id,
                            Document.id != doc_id
                        ).limit(1))
                    if r := r.first():
                        (uri_eq,) = r
                        await uri_equivalence.merge(session, uri_eq, doc.uri)
                        doc.delete()
                        await session.commit()
                        logger.warn(f"Document with this file already exists at URL {uri_eq.uri}")
                        return
        session.add(doc)
        await session.commit()
        base_type = doc.mimetype.split(';')[0]
        if new_data:
            if (base_type == "application/pdf"):
                await get_channel("process_pdf").send_soon(key=str(doc_id), value=doc_id)
            elif (base_type == "text/html"):
                await get_channel("process_html").send_soon(key=str(doc_id), value=doc_id)
            elif (base_type in ("text/plain", "text/markdown")):
                await get_channel("process_text").send_soon(key=str(doc_id), value=doc_id)
        return base_type if new_data else None
