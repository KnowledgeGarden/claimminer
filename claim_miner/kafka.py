"""
Utility functions to define the set of kafka topics and obtaining consumer and producers for those topics.
"""
# Copyright Society Library and Conversence 2022-2023
import logging
import aiokafka

import simplejson as json

from . import config

logger = logging.getLogger("event")
CONSUMER = None
PRODUCER = None

topics = [
    "debatemap",
    "download",
    "embed",
    "gdelt",
    "process_html",
    "process_pdf",
    "process_text",
]


def serializer(j):
    return json.dumps(j).encode("utf-8") if j else None

def deserializer(s):
    return json.loads(s.decode("utf-8")) if s else None


async def get_consumer():
    global CONSUMER, logger
    if CONSUMER is None:
        CONSUMER = aiokafka.AIOKafkaConsumer(
            *topics,
            bootstrap_servers=f"{config.get('kafka', 'host', fallback='localhost')}:{config.get('kafka', 'port', fallback=9092)}",
            value_deserializer=deserializer, key_deserializer=deserializer,
            group_id='ClaimMiner')
        await CONSUMER.start()
        logger.info("Consumer ready")
    return CONSUMER


async def get_producer():
    global PRODUCER
    if PRODUCER is None:
        PRODUCER = aiokafka.AIOKafkaProducer(
            bootstrap_servers=f"{config.get('kafka', 'host', fallback='localhost')}:{config.get('kafka', 'port', fallback=9092)}",
            value_serializer=serializer, key_serializer=serializer)
        await PRODUCER.start()
        logger.info("Producer ready")
    return PRODUCER


async def stop_producer():
    global PRODUCER
    if PRODUCER is not None:
        await PRODUCER.stop()
        logger.info("Producer stopped")
    PRODUCER = None


async def stop_consumer():
    global CONSUMER
    if CONSUMER is not None:
        await CONSUMER.stop()
        logger.info("Consumer stopped")
    CONSUMER = None

class TopicWrapper():
    def __init__(self, topic):
        self.topic = topic

    async def send_soon(self, value, key=None):
        producer = await get_producer()
        await producer.send(self.topic, value, key)

wrappers = {t: TopicWrapper(t) for t in topics}

def get_channel(topic):
    return wrappers[topic]
