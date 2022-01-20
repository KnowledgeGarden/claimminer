ClaimMiner
==========

Functional overview
-------------------

ClaimMiner was designed for SocietyLibrary as a way to identify claim networks in a document corpus.
Over time, it is evolving to be in symbiosis with DebateMap_, and may eventually merge with it.

The expected main data flow is as follows:

1. The documents are added to the corpus, either uploaded directly or as URLs
2. URLs are downloaded
3. Documents are broken into paragraphs
4. Language embeddings are calculated for each paragraph
5. Operators input some initial seed claims (or import them from DebateMap)
6. Operators look for semantically related paragraphs in the corpus using the embeddings
7. They send the most promising paragraphs to AI systems that will identify claims in the paragraphs
8. Those claims are vetted, stored in the system and eventually sent to DebateMap.

There are other ancillary functions:

1. ClaimMiner can use GDELT_ to perform a semantic search for news items
2. ClaimMiner can identify clusters in the claims, and draw a cloud of claims
3. ClaimMiner can perform text search on paragraphs or claims
4. ClaimMiner can perform a broadening semantic search (MMR) on paragraphs or claims
5. ClaimMiner acts as a proxy for Paper's CTE

Technology stack
----------------

* ClaimMiner's data is mostly stored in Postgres (14 or better). In particular, storing the embeddings requires the use of pgvector_.
* The web server is built using Quart_, an asyncio equivalent to Flask_.
* It is a classic backend with Jinja_ templates, i.e. not a SPA.
* It uses SQLAlchemy_ to talk to the database.
* It uses kafka_ to send work requests to a worker.
* It is served using hypercorn_, through an nginx_ proxy.
* Uploaded or downloaded documents are stored in the file system, and the database keeps a hash reference.
* Some machine learning operations (clustering, tag clouds) are done using scikit-learn_.
* The MMR is computed within the database, with a pl-python_ procedure.
* Database migrations are run using the db_updater_ script.
* Claim identification currently uses OpenAI_ through langchain_.
* Server-side sessions are cached in redis_
* Some more caching is done using memcached_

.. _Postgres: https://www.postgresql.org
.. _DebateMap: https://github.com/debate-map/app
.. _pgvector: https://github.com/pgvector/pgvector
.. _GDELT: https://www.gdeltproject.org/
.. _langchain: https://github.com/hwchase17/langchain
.. _Quart: https://pgjones.gitlab.io/quart/
.. _Flask: https://flask.palletsprojects.com/en/
.. _Jinja: https://jinja.palletsprojects.com/en/
.. _SQLAlchemy: https://www.sqlalchemy.org/
.. _hypercorn: https://pgjones.gitlab.io/hypercorn/
.. _nginx: https://nginx.org
.. _scikit-learn: https://scikit-learn.org/stable/
.. _OpenAI: https://openai.com
.. _pl-python: https://www.postgresql.org/docs/current/plpython.html
.. _kafka: https://kafka.apache.org
.. _redis: https://redis.com
.. _memcached: https://memcached.org
.. _db_updater: db_updater.html
