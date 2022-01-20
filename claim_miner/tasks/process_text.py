"""
Copyright Society Library and Conversence 2022-2023
"""
from pathlib import Path
import re

from sqlalchemy.future import select
from langdetect import detect
from sqlalchemy.sql.functions import count

from .. import Session, hashfs, get_analyzer_id, run_sync
from ..models import Document, Fragment
from . import logger, schedule_fragment_embeds

version = 1

MIN_PARAGRAPH_LENGTH = 120

def collapse_whitespace_and_paras(s):
    s = re.sub(r'[ \t\f\v\xa0]+', ' ', s)
    return re.sub(r'([ \t\f\v\xa0]?\n[ \t\f\v\xa0]?)+', '\n', s).strip()

def collapse_whitespace_with_paras(s):
    return re.sub(r'\s+', ' ', s).strip()

async def do_process_text(doc_id):
    new_data = False
    fragments = []
    analyzer_id = await get_analyzer_id("process_text", version)
    async with Session() as session:
        r = await session.execute(
            select(Document).filter_by(id=doc_id))
        doc = r.first()
        if not doc:
            logger.error(f"Missing document {doc_id}")
            return False, []

        doc = doc[0]
        text_id = hashfs.get(doc.text_identity or doc.file_identity)
        doc.text_identity = text_id.id

        def do_get_text():
            with open(text_id.abspath, 'r') as f:
                text = f.read()
            paras = text.split("\n")
            return text, paras, detect(text)
        text, paras, lang = await run_sync(do_get_text)()
        doc.language = lang
        # check if the paragraphs are missing
        num_paras = await session.scalar(select(count(Fragment.id)).filter_by(doc_id=doc_id))
        new_data = num_paras == 0
        if new_data:
            doc.text_size = Path(text_id.abspath).stat().st_size
            doc.text_analyzer_id = analyzer_id
            session.add(doc)
            char_pos = 0
            for (para_pos, para) in enumerate(paras):
                if len(para) >= MIN_PARAGRAPH_LENGTH:
                    # TODO: Check if exists. If so, update. (Unique index?)
                    f = Fragment(text=para, doc_id=doc_id, char_position=char_pos, position=para_pos, scale="paragraph", language=doc.language)
                    session.add(f)
                    fragments.append(f)
                char_pos += 1 + len(para)
        else:
            return False, []
        await session.commit()
    await schedule_fragment_embeds([f.id for f in fragments], doc_id=doc_id)
    return True
