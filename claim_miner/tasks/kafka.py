"""
The main worker loop for asynchronous events. Dispatches kafka messages to various tasks.
"""
# Copyright Society Library and Conversence 2022-2023
# TODO: create a worker pool.
import logging
import asyncio
import atexit
import traceback

from .. import get_analyzer_id, config, kafka as kafka_module
from ..kafka import get_consumer, stop_consumer, stop_producer, logger
from .debatemap import do_debatemap
from .download import do_download
from .embed import do_embed_doc, do_embed_fragment, version
from .gdelt import do_gdelt
from .process_html import do_process_html
from .process_pdf import do_process_pdf
from .process_text import do_process_text

RUNNING = True
if "event_logging" in config:
    logging.basicConfig(**dict(config.items("event_logging", {})))


async def worker():
    global RUNNING
    consumer = await get_consumer()
    logger.info("Consumer ready")
    async for msg in consumer:
        if not RUNNING:
            break
        logger.debug(f"received %s %s", msg.topic, msg.value)
        try:
            if msg.topic == "debatemap":
                params = msg.value.split()
                if len(params) == 2:
                    claim_id, depth = params
                    depth = int(depth)
                elif len(params) == 1:
                    claim_id = params[0]
                    depth = 1
                await do_debatemap(claim_id, depth)
            elif msg.topic == "download":
                doc_id = int(msg.value)
                await do_download(doc_id)
            elif msg.topic == "embed":
                ev_val = msg.value.split()
                analyzer_id = await get_analyzer_id("embed", version)
                ev_val, model = ev_val if len(ev_val) > 1 else (ev_val[0], None)
                if ev_val.startswith('D'):
                    await do_embed_doc([int(ev_val[1:])], analyzer_id, model)
                elif ev_val.startswith('F'):
                    await do_embed_fragment([int(ev_val[1:])], analyzer_id, model)
            elif msg.topic == "gdelt":
                request = msg.value
                claim_id = request['claim']
                source = request.get('source', 'docs')
                limit = request.get('limit', 10)
                date = request.get('since', None)
                await do_gdelt(claim_id, source, limit, date)
            elif msg.topic == "process_html":
                doc_id = int(msg.value)
                await do_process_html(doc_id)
            elif msg.topic == "process_pdf":
                params = msg.value
                if isinstance(params, dict):
                    doc_id = params.pop("doc_id")
                else:
                    doc_id, params = int(params), {}
                await do_process_pdf(doc_id, params)
            elif msg.topic == "process_text":
                doc_id = int(msg.value)
                await do_process_text(doc_id)
        except Exception as e:
            traceback.print_exception(e)
        logger.info("done %s %s", msg.topic, msg.value)



def exit_handler():
    global RUNNING
    RUNNING = False

atexit.register(exit_handler)

async def finish():
    await stop_consumer()
    await stop_producer()

async def run_and_stop():
    try:
        await worker()
    finally:
        await finish()

if __name__ == "__main__":
    # TODO: Multiple workers with a semaphore
    asyncio.run(run_and_stop())
