"""
Copyright Society Library and Conversence 2022-2023
"""
from io import BytesIO
from pathlib import Path
import re

from bs4 import BeautifulSoup
from langdetect import detect
from sqlalchemy import delete
from sqlalchemy.future import select
from sqlalchemy.sql.functions import count

from .. import Session, get_analyzer_id, hashfs, run_sync
from ..models import Document, Fragment
from . import logger, schedule_fragment_embeds

def collapse_whitespace_and_paras(s):
    s = re.sub(r'[ \t\f\v\xa0]+', ' ', s)
    return re.sub(r'([ \t\f\v\xa0]?\n[ \t\f\v\xa0]?)+', '\n', s).strip()


def collapse_whitespace_with_paras(s):
    return re.sub(r'\s+', ' ', s).strip()


MIN_PARAGRAPH_LENGTH = 120
version = 1


async def do_process_html(doc_id):
    analyzer_id = await get_analyzer_id("process_html", version)
    fragments = []
    new_data = False
    async with Session() as session:
        r = await session.execute(
            select(Document).filter_by(id=doc_id))
        doc = r.first()
        if not doc:
            logger.error(f"Missing document {doc_id}")
            return False, []

        doc = doc[0]
        old_text_id = doc.text_identity
        file = hashfs.get(doc.file_identity)

        def do_get_text():
            with open(file.abspath, 'r') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')
            text = soup.get_text()
            paras = soup.find_all('p')
            paras = [p.get_text() for p in paras]
            # heuristics
            use_paras = sum(len(p) for p in paras) >= 0.8 * len(text)
            if use_paras:
                paras = [collapse_whitespace_with_paras(p) for p in paras]
                text = '\n'.join(paras)
            else:
                text = collapse_whitespace_and_paras(text)
                paras = text.split('\n')
            return soup, text, paras, detect(text)
        soup, text, paras, lang = await run_sync(do_get_text)()
        doc.language = lang
        text_address = hashfs.put(BytesIO(text.encode('utf-8')))
        new_data = doc.text_identity != text_address.id
        if not new_data:
            # check if the paragraphs are missing
            num_paras = await session.scalar(select(count(Fragment.id)).filter_by(doc_id=doc_id))
            new_data = num_paras == 0
        if new_data:
            if doc.text_identity:
                # TODO: check if used by another document
                hashfs.delete(doc.text_identity)
                await session.execute(delete(Fragment).where(doc_id=doc_id))
            doc.text_identity = text_address.id
            doc.text_size = Path(text_address.abspath).stat().st_size
            doc.text_analyzer_id = analyzer_id
            session.add(doc)
            char_pos = 0
            for (para_pos, para) in enumerate(paras):
                if len(para) >= MIN_PARAGRAPH_LENGTH:
                    # TODO: Check if exists. If so, update. (Unique index?)
                    f = Fragment(text=para, doc_id=doc_id, char_position=char_pos,
                                    position=para_pos, scale="paragraph", language=doc.language)
                    session.add(f)
                    fragments.append(f)
                char_pos += 1 + len(para)
        else:
            return False, []
        await session.commit()
    if old_text_id and old_text_id != text_address.id:
        hashfs.delete(old_text_id)
    await schedule_fragment_embeds([f.id for f in fragments], doc_id=doc_id)
    return True

