"""
Copyright Society Library and Conversence 2022-2023
"""
import numpy as np
from asyncio import sleep
from . import config, run_sync
from .models import BASE_EMBED_MODEL

USE4 = None
ADA2 = None

def get_use4():
    global USE4
    if USE4 is None:
        import tensorflow_hub as hub
        import tensorflow as tf
        model = hub.load("https://tfhub.dev/google/universal-sentence-encoder/4")
        USE4 = lambda text: normalization(model(tf.constant([text])))[0].numpy().astype(float)
    return USE4


def get_openai():
    global ADA2
    if ADA2 is None:
        import openai
        openai.organization = config.get("openai", "organization")
        openai.api_key =  config.get("openai", "api_key")
        ADA2 = openai.Embedding
    return ADA2


def normalization(embeds):
    norms = np.linalg.norm(embeds, 2, axis=1, keepdims=True)
    return embeds/norms


async def tf_embed(text, model=BASE_EMBED_MODEL):
    if model == BASE_EMBED_MODEL:
        if isinstance(text, list):
            result = []
            for t in text:
                result.append(await tf_embed(t, model))
            return result
        return await run_sync(get_use4())(text)
    elif model == 'txt_embed_ada_2':
        from openai.error import RateLimitError
        if is_single := not isinstance(text, list):
            text = [text]
        # TODO: Batch by size, handle oversize...
        while True:
            try:
                results = await get_openai().acreate(model="text-embedding-ada-002", input=text)
                break
            except RateLimitError:
                await sleep(20)
        results = [r['embedding'] for r in results['data']]
        if is_single:
            results = results[0]
        return results
    else:
        raise RuntimeError(f"Unknown model: {model}")
