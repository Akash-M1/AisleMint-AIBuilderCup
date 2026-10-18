import os
from pathlib import Path
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
# Validate before importing application modules or opening any database connection.
_test_db_url=os.environ.get('TEST_DATABASE_URL')
if not _test_db_url:
    _test_db_url=make_conninfo(host=os.environ.get('TEST_POSTGRES_HOST','127.0.0.1'),
        port=os.environ.get('TEST_POSTGRES_PORT','55432'),dbname='aislemint_test',
        user=os.environ.get('TEST_POSTGRES_USER','aislemint_test'),password=os.environ.get('TEST_POSTGRES_PASSWORD','local-review-only'))
_info=conninfo_to_dict(_test_db_url)
if _info.get('host') not in ('127.0.0.1','localhost') or _info.get('dbname')!='aislemint_test':
    raise RuntimeError('Tests require a loopback disposable database named aislemint_test. Production targets are forbidden.')
os.environ['DATABASE_URL']=_test_db_url
os.environ['INTEGRATION_API_KEY']='test-integration-key'
os.environ['AISLEMINT_TEST_AUTH_BYPASS']='1'
os.environ['RETAIL_TRAINING_WORKER']='external'
os.environ['CONNECTED_WORKER']='external'
os.environ['DATA_PLATFORM_WORKER']='external'
from promo.engine import Engine

@pytest.fixture(scope='session')
def engine():
    artifact=os.environ.get('PROMO_TEST_ARTIFACT_DIR')
    return Engine(artifact_dir=Path(artifact) if artifact else None)

@pytest.fixture(scope='session',autouse=True)
def pg_test_database():
    from promo.pg import initialize,pool
    initialize()
    with pool().connection() as conn:
        if not conn.execute("SELECT 1 FROM assets WHERE key='data/processed/panel.parquet'").fetchone():
            pytest.fail('Seed the disposable PostgreSQL database with scripts/seed_test_pg.py first.')
    yield
    pool().close()

@pytest.fixture(autouse=True)
def clean_operational_collections(pg_test_database):
    from promo.pg import pool
    from psycopg import sql
    keep={'assets','asset_versions','asset_blobs','schema_versions'}
    with pool().connection() as conn:
        tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'") if r[0] not in keep]
        conn.execute(sql.SQL('TRUNCATE {}').format(sql.SQL(',').join(map(sql.Identifier,tables))))
        for table in ('assets','asset_versions'):
            conn.execute(sql.SQL("DELETE FROM {} WHERE key ~ '^(tests?/|retail/)'").format(sql.Identifier(table)))
    yield
