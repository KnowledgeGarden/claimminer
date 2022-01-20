"""
Copyright Society Library and Conversence 2022-2023
"""
import re
from io import BytesIO
from pathlib import Path
import argparse

from langdetect import detect
from pdfminer.high_level import extract_text
from sqlalchemy import delete
from sqlalchemy.future import select
from sqlalchemy.sql.functions import count

from .. import Session, get_analyzer_id, hashfs, run_sync
from ..models import Document, Fragment
from ..nlp import breakup_sentences
from . import logger, schedule_fragment_embeds

version = 1
MIN_PARAGRAPH_LENGTH = 120


async def do_process_pdf(doc_id, process_params=None):
    fragments = []
    new_data = False
    process_params = process_params or {}
    analyzer_id = await get_analyzer_id("process_pdf", version)
    async with Session() as session:
        r = await session.execute(
            select(Document).filter_by(id=doc_id))
        doc = r.first()
        if not doc:
            logger.error(f"Missing document {doc_id}")
            return False, []
        doc = doc[0]
        new_data = False
        reparse = process_params.get("reparse", False)
        split_algo = process_params.get("split_algo", None)
        original_split_algo = doc.process_params.get("split_algo", None)
        if reparse or (split_algo != original_split_algo) or not doc.text_identity:
            f = hashfs.get(doc.file_identity)

            def do_extract():
                return extract_text(f.abspath)

            text = await run_sync(do_extract)()
            paras = re.split(r'\n\n+', text)
            # Convert single newlines to spaces
            paras = [re.sub(r'\s\s+', " ", re.sub(r'\s*\n\s*', ' ', p.strip())) for p in paras if p.strip()]
            text = '\n'.join(paras)
            text_address = hashfs.put(BytesIO(text.encode('utf-8')))
            new_data = doc.text_identity != text_address.id
        if not new_data:
            # check if the paragraphs are missing
            num_paras = await session.scalar(select(count(Fragment.id)).filter_by(doc_id=doc_id, scale='paragraph'))
            new_data = num_paras == 0
        if new_data:
            if doc.text_identity:
                # TODO: check if used by another document
                hashfs.delete(doc.text_identity)
                await session.execute(delete(Fragment).where(doc_id=doc_id))
            doc.text_identity = text_address.id
            doc.text_size = Path(text_address.abspath).stat().st_size
            doc.text_analyzer_id = analyzer_id
            doc.language = detect(text)
            session.add(doc)
            char_pos = 0
            for (para_pos, para) in enumerate(paras):
                if len(para) >= MIN_PARAGRAPH_LENGTH:
                    # TODO: Check if exists. If so, update. (Unique index?)
                    f = Fragment(text=para, doc_id=doc_id, char_position=char_pos, position=para_pos, scale="paragraph",
                                 language=doc.language)
                    session.add(f)
                    fragments.append(f)
                char_pos += 1 + len(para)
        else:
            return False, []
        await session.commit()
    await schedule_fragment_embeds([f.id for f in fragments], doc_id=doc_id)
    return new_data
