"""
Copyright Society Library and Conversence 2022-2023
"""
from pathlib import Path
from google.cloud import bigquery
from sqlalchemy import cast, ARRAY, Float
from sqlalchemy.future import select
from google.auth import load_credentials_from_file

from .. import get_analyzer_id, Session, config, run_sync
from ..models import Embedding_Use4 as Embedding, Fragment, Document, UriEquiv
from ..kafka import get_channel
from . import logger

version = 1

client = None
if credential_filename := config.get("base", "google_credentials", fallback=None):
    credentials, project_id = load_credentials_from_file(
        Path(__file__).parent.parent.parent.joinpath(credential_filename)
    )
    # see https://cloud.google.com/bigquery/docs/reference/libraries
    # assumes GOOGLE_APPLICATION_CREDENTIALS points in the right place.
    client = bigquery.Client(credentials=credentials)


query_data = {
    'news': {
        'table': 'gdelt-bq.gdeltv2.gsg_iatvsentembed',
        'embed_col': 'sentEmbed',
        'url_col': 'previewUrl',
        'where_': '',
    },
    'docs': {
        'table': 'gdelt-bq.gdeltv2.gsg_docembed',
        'embed_col': 'docEmbed',
        'url_col': 'url',
        'where_': " AND lang='ENGLISH' ",
    }
}


query = """
CREATE TEMPORARY FUNCTION cossim(a ARRAY<FLOAT64>, b ARRAY<FLOAT64>)
RETURNS FLOAT64 LANGUAGE js AS '''
var sumt=0, suma=0, sumb=0;
for(i=0;i<a.length;i++) {{
sumt += (a[i]*b[i]);
suma += (a[i]*a[i]);
sumb += (b[i]*b[i]);
}}
suma = Math.sqrt(suma);
sumb = Math.sqrt(sumb);
return sumt/(suma*sumb);
''';

WITH query AS (
select [{embed}] as sentEmbed
)
SELECT cossim(t.embed, query.sentEmbed) sim, t.date, t.url, t.embed
FROM
(
    SELECT {embed_col} embed, date, {url_col} url, ROW_NUMBER() OVER (PARTITION BY url ORDER BY date desc) rn
    FROM `gdelt-bq.gdeltv2.gsg_docembed`
    WHERE model='USEv4' {where_}
) t, query
WHERE rn = 1
order by sim desc limit {limit}
"""


async def do_gdelt(claim_id, source='docs', limit=10, date=None):
    analyzer_id = await get_analyzer_id("gdelt", version)
    document_ids = []
    async with Session() as session:
        r = await session.scalar(
            select(Embedding.embedding
                ).join(Fragment, Embedding.fragment_id==Fragment.id
                ).filter(Fragment.id==claim_id, Fragment.scale=='standalone'
                ).limit(1))
        embed = r.rstrip(')').lstrip('(')
        terms = dict(**query_data[source])
        if date is not None:
            terms['where_'] += f' AND date >= "{date}"'
        queryt=query.format(embed=embed, limit=limit, **terms)
        def analyze():
            return query(queryt)

        query_job =  await run_sync(analyze)()
        for row in query_job:
            try:
                url = row.url
                r = await session.scalar(
                    select(Document.id).filter(Document.url==url).limit(1))
                if r:
                    continue
                # Assume the gdelt URLs are canonical for now...
                uri = UriEquiv(uri=url, status='canonical')
                document = Document(uri=uri, language='en')
                embedding = Embedding(
                    document=document, embedding=cast(row.embed, ARRAY(Float)), scale='document', analyzer_id=analyzer_id)
                session.add(document)
                session.add(embedding)
                await session.commit()
                document_ids.append(document.id)
            except Exception as e:
                logger.exception("")
                await session.rollback()
    download_channel = get_channel('download')
    for doc_id in document_ids:
        await download_channel.send_soon(key=str(doc_id), value=doc_id)
    return document_ids
