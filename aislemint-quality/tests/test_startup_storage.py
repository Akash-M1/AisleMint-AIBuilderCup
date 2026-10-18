"""Persistence initialization, pool and completed-migration boundaries."""
import pytest
from promo import pg

def test_current_schema_initialization_uses_no_schema_rewrite():
    with pg.pool().connection() as conn:
        before=conn.execute('SELECT version,applied_at FROM schema_versions ORDER BY version').fetchall()
    pg._initialized.clear();pg.initialize()
    with pg.pool().connection() as conn:
        assert conn.execute('SELECT version,applied_at FROM schema_versions ORDER BY version').fetchall()==before

def test_connections_are_returned_after_many_operations():
    db=pg.database()
    for _ in range(100):db.retail_users.count_documents({})
    stats=pg.pool().get_stats()
    assert stats['pool_available']==stats['pool_size']

def test_no_legacy_jobs_needs_no_training_lease(monkeypatch):
    from promo import pos_learning
    from promo.jobs import training_lease
    monkeypatch.setattr(training_lease,'acquire',lambda **_:pytest.fail('Do not create a lease for an empty queue'))
    pos_learning.recover()

def test_named_query_repeats_and_reorders_parameters():
    with pg.pool().connection() as conn:
        query,params=pg.bind_sql('SELECT $2::int, $1::int, $2::int',(3,7))
        assert conn.execute(query,params).fetchone()==(7,3,7)

def test_all_named_read_queries_compile():
    import re
    with pg.pool().connection() as conn:
        for name,spec in pg.PLANS.items():
            if name in pg.WRITE_PLANS:continue
            count=max([int(v) for v in re.findall(r'\$(\d+)',spec['sql'])] or [0])
            query,params=pg.bind_sql(spec['sql'],[None]*count)
            try:conn.execute('EXPLAIN '+query,params)
            except Exception as exc:pytest.fail(name+': '+type(exc).__name__)
