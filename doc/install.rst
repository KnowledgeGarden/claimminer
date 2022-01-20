Installation
============

ClaimMiner has been installed on Ubuntu and MacOS. Familiarity with most common components and installation procedures is assumed.

Prerequisites: Ubuntu Jammy
---------------------------

.. code-block:: shell-session

    sudo apt install python3.10-dev postgresql-server-dev-15 memcached redis librdkafka-dev nginx

Kafka is installed by hand, you could follow `these instructions <https://www.conduktor.io/kafka/how-to-install-apache-kafka-on-linux-without-zookeeper-kraft-mode/>`_

Prerequisites: Mac
------------------

.. code-block:: shell-session

    brew install python@3.10 postgresql@14 redis memcached kafka md5sha1sum
    brew tap conversence/ConversenceTaps
    brew install postgresql_plpy@14
    brew services start postgresql@14 redis memcached zookeeper kafka


Notes on prerequisites
----------------------

* Python 3.10 is assumed, as found on Ubuntu Jammy. It is likely 3.11 would work.
* On mac, tensorflow-text has only been built for python 3.10 (or 3.9). You can find wheels `here <https://github.com/sun1638650145/Libraries-and-Extensions-for-TensorFlow-for-Apple-Silicon/releases>`_.
* Postgres 15 is used on Ubuntu, but on mac, the homebrew postgres 15 recipe does not handle extensions as well as 14.
* It is simpler to operate Kafka in KRaft mode, you don't need zookeeper. But on mac, it's simple to follow the default with zookeeper.
* Nginx is for production, I did not install it on Mac.

PgVector
--------

Install pgvector from `source <https://github.com/pgvector/pgvector>`_, following instructions. (A traditional ``make ; sudo make install``.)

It may be necessary to set ``PG_CONFIG`` to the appropriate path:
``/usr/lib/postgresql/15/bin/pg_config`` on linux, ``/opt/homebrew/opt/postgresql@14/bin/pg_config`` on mac.

ClaimMiner
----------

1. Clone the repository and ``cd`` into it
2. Create a virtual environment (``python3.10 -mvenv venv``) and activate it (``. ./venv/bin/activate``)
3. Install the application (``pip install -e .``)
4. Create a skeleton config.ini file by calling initial setup. Exact arguments will depend on platform. The point is to pass database administrator credentials.

  1. Ubuntu, assuming a postgres user exists, and the current user is a sudoer:

    1. ``python scripts/initial_setup.py --app_name ClaimMiner --sudo -u postgres``
    2. Note: I have a non-sudoer user to run ClaimMiner, but login as a sudoer user when necessary for some commands.

  2. Mac, assuming the database accepts the logged-in user as a database admin:

    1. ``python scripts/initial_setup.py --app_name ClaimMiner``

  3. Note that calls to ``initial_setup.py`` can be repeated without losing information. More options are given in the ``--help``

5. Initialize the development database

  1. ``python scripts/db_updater.py init``
  2. ``python scripts/db_updater.py deploy``
  3. The last command can and should be reapplied to run migrations whenever changes are made to the database schema.
  4. The need to do so can be verified with ``python scripts/db_updater.py status``.
  5. Note: The initial deployment may require a sudoer user on ubuntu.

6. Ensure the tensorflow cache is not in temp. (I set ``export TFHUB_CACHE_DIR=$HOME/.cache/tfhub_modules`` in my .bashrc)


Credentials
-----------

Then, some more credentials need to be added to the ``config.ini``. The following sections or variables need to be added.
(TODO: Add a template for this.)

.. code-block:: ini

    [base]
    google_credentials = <filename>
    spacy_model = en_core_web_sm

    [cse]
    cx = <cse identity>
    google_credentials = <filename>

    [openai]
    api_key = <key>
    organization = <org_id>

    [debatemap]
    base_url = https://debates.app/debates/
    graphql_endpoint = https://app-server.debates.app/graphql
    graphql_referer = https://app-server.debates.app/graphiql-new
    token = <debatemap_token>

    [web_logging]
    filename = web.log
    level = INFO

    [event_logging]
    filename = events.log
    level = INFO

Here is where and how to obtain each credential:

DebateMap
.........

1. Login with a google account on https://debates.app . Your gmail username will be used by DebateMap.
2. Visit https://app-server.debates.app/gql-playground
3. Use query and data below, then follow instructions from the query results. Record the token.

.. code-block: gql

    subscription($input: SignInStartInput!) {
      signInStart(input: $input) {
        instructions
        authLink
        resultJWT
      }
    }

    # Variables:

    {
      "input":{
      "provider": "google",
      "jwtDuration": 7776000,
      "jwtReadOnly": false,
      "preferredUsername": "<username>"
      }
    }


Google credentials
..................

We use two sets of credentials, one for CSE, and one for GDELT. Neither is strictly essential. The same credentials could be used for both.

`Create a project <https://console.cloud.google.com/projectcreate>`_ in the Google console, or reuse one you have; then `create a service account <https://console.cloud.google.com/iam-admin/serviceaccounts>`_ for that project; then create keys for that account (follow the console) and download the key pair as a json file. Place that json file in the file root, and give the filename as credentials.

Then you have to `activate the necessary services <https://console.cloud.google.com/apis/library>`_.
Here is a list of currently activated APIs for the GDELT account (It is possible that all are not necessary...)

* BigQuery API
* BigQuery Reservation API
* BigQuery Storage API
* Cloud Datastore API
* Cloud Debugger API
* Cloud Logging API
* Cloud Monitoring API
* Cloud SQL
* Cloud Storage
* Cloud Storage API
* Cloud Trace API
* Custom Search API
* Google Cloud APIs
* Google Cloud Storage JSON API
* Service Management API
* Service Usage API

You will also have to `define a quota <https://console.cloud.google.com/apis/api/bigquery.googleapis.com/quotas>`_ for the use of BigQuery on the GDELT account. The queries are usually quite expensive, as there is currently no indexing on the embeddings.

TODO: List the APIs activated for the CSE account.

Running (development)
---------------------

In different terminals, where the virtualenv has been activated, run the two following commands:

* ``python -m claim_miner.tasks.kafka``
* ``env QUART_APP=claim_miner/app_full.py quart run --reload``

Production installation
-----------------------

(To be developed)

* Setup systemd tasks for the web and worker
* Set the ``PRODUCTION=1`` environment variable for the kafka task
* The web task will go through hypercorn: ``<path to venv>/bin/hypercorn --config hypercorn.toml claim_miner.app_full``
* Setup a nginx reverse proxy on the hypercorn port. (Select a free port on your machine.)

