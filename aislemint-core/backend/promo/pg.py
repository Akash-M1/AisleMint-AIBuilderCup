"""PostgreSQL repository and transaction boundary. Drop-in replacement for mongo.py.

Connection pool, transaction management, and query dispatch for PostgreSQL.
The UnitOfWork API (`run`, `insert`, `aggregate`) is preserved so all callers
(pos_api, promotions, demand, etc.) keep their `c.run('query_name', params)` calls.
"""
from __future__ import annotations
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import hashlib, json, os, re, time, threading
from datetime import datetime, timezone
from contextvars import ContextVar
from decimal import Decimal
_active_connection = ContextVar("aislemint_pg_connection", default=None)
from dotenv import load_dotenv

from .runtime import ROOT, ENV_FILE
load_dotenv(ENV_FILE, override=False)

SCHEMA_SQL = (Path(__file__).parent / 'pg_schema.sql').read_text()
PLANS = json.loads((Path(__file__).parent / 'pg_queries.json').read_text())
JSON_FIELDS = {'request', 'result', 'quality', 'snapshot', 'payload', 'body'}
_lock = threading.Lock()
_initialized = set()
_migration_checked = set()


# ---------------------------------------------------------------------------
# Connection Pool
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def pool():
    """Create and return a psycopg connection pool."""
    import psycopg_pool

    conninfo = _conninfo()
    if not conninfo:
        raise RuntimeError(
            'PostgreSQL is not configured. Set POSTGRES_HOST and POSTGRES_DATABASE '
            '(or DATABASE_URL). MongoDB fallback is disabled.'
        )
    result = psycopg_pool.ConnectionPool(
        conninfo,
        open=True,
        min_size=0,
        max_size=int(os.environ.get('POSTGRES_POOL_SIZE', '10')),
        timeout=8.0,
        max_idle=300.0,
        kwargs={
            'autocommit': False,
            'options': '-c statement_timeout=120000 -c default_transaction_isolation=serializable',
        },
    )

    import atexit
    atexit.register(result.close)
    return result


def _conninfo() -> str:
    """Build a libpq connection string from environment variables."""
    url = os.environ.get('DATABASE_URL', '')
    if url:
        return url
    host = os.environ.get('POSTGRES_HOST', '')
    if not host:
        return ''
    port = os.environ.get('POSTGRES_PORT', '5432')
    dbname = os.environ.get('POSTGRES_DATABASE', '')
    user = os.environ.get('POSTGRES_USER', '')
    password = os.environ.get('POSTGRES_PASSWORD', '')
    if not dbname:
        raise RuntimeError('Set POSTGRES_DATABASE to a valid database name.')

    from psycopg.conninfo import make_conninfo
    values = dict(host=host, port=port, dbname=dbname, user=user, password=password,
                  connect_timeout=8, application_name='AisleMint')
    for key in ('sslmode', 'sslrootcert', 'sslcert', 'sslkey'):
        value = os.environ.get('POSTGRES_' + key.upper())
        if value: values[key] = value
    if any(str(v).startswith('<') for v in values.values()):
        raise RuntimeError('Replace PostgreSQL environment placeholders before connecting.')
    return make_conninfo(**values)


def check_connection():
    """Verify connectivity and return the database name."""
    with operation_connection(read_only=True) as conn:
        row = conn.execute('SELECT current_database()').fetchone()
        return row[0]


def assert_migration_ready():
    with pool().connection() as conn:
        if conn.execute("SELECT to_regclass('public.mongo_import')").fetchone()[0]:
            if conn.execute("SELECT 1 FROM mongo_import WHERE NOT verified OR status <> 'verified' LIMIT 1").fetchone():
                raise RuntimeError('PostgreSQL migration has not been verified. Complete the import before starting the application.')


# ---------------------------------------------------------------------------
# Schema Initialization
# ---------------------------------------------------------------------------

# Load the old mongo_schema.json for primary_keys() and defaults() compatibility
_MONGO_SCHEMA_PATH = Path(__file__).parent / 'pg_metadata.json'
SCHEMA = json.loads(_MONGO_SCHEMA_PATH.read_text()) if _MONGO_SCHEMA_PATH.exists() else {}


def primary_keys(name):
    if name == 'promotion_events': return ['campaign_id','store_id','product_id','period_end']
    if name == 'pos_events':
        return ['store_id', 'sequence']
    if name == 'audit':
        return ['workspace', 'id']
    if name not in SCHEMA:
        return ['_id']
    return [f['name'] for f in sorted(SCHEMA[name]['fields'], key=lambda f: f['pk']) if f['pk']]


def defaults(name):
    if name not in SCHEMA:
        return {}
    result = {}
    for f in SCHEMA[name]['fields']:
        v = f['default']
        if v is None:
            result[f['name']] = None
        elif v.startswith("'"):
            result[f['name']] = v[1:-1]
        else:
            result[f['name']] = int(v)
    return result


def encode_values(values):
    return {
        k: json.loads(v) if k in JSON_FIELDS and isinstance(v, str) else v
        for k, v in values.items()
    }


def _pg_value(key, value):
    """Convert a Python value to PostgreSQL-compatible representation."""
    import psycopg.types.json
    if key in JSON_FIELDS and isinstance(value, (dict, list)):
        return psycopg.types.json.Jsonb(value)
    return value


def decode_row(row, fields=None):
    if row is None:
        return None
    keys = fields or list(row.keys())
    result = {}
    for k in keys:
        v = row.get(k) if hasattr(row, 'get') else row[k]
        if isinstance(v, Decimal): v = int(v) if v == v.to_integral_value() else float(v)
        if k in JSON_FIELDS and isinstance(v, (dict, list)):
            result[k] = json.dumps(v, separators=(',', ':'))
        else:
            result[k] = v
    return Row(result)


class Row(dict):
    """Dict subclass supporting integer-indexed access for positional column lookup."""
    def __getitem__(self, key):
        return super().__getitem__(list(self)[key] if isinstance(key, int) else key)


class Result:
    """Cursor-like result wrapper for compatibility with existing callers."""
    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.index = 0

    def fetchone(self):
        if self.index >= len(self.rows):
            return None
        row = self.rows[self.index]
        self.index += 1
        return row

    def fetchall(self):
        result = self.rows[self.index:]
        self.index = len(self.rows)
        return result

    def __iter__(self):
        return iter(self.fetchall())


# ---------------------------------------------------------------------------
# Initialize Schema
# ---------------------------------------------------------------------------

def initialize(*, allow_incomplete_migration=False):
    """Create all tables and indexes from pg_schema.sql (idempotent)."""
    key = _conninfo()
    # Readiness is a startup gate, not a query before every business transaction.
    # Keep a separate marker so an importer cannot bypass the normal startup gate.
    if key in _initialized and (allow_incomplete_migration or key in _migration_checked):
        return
    with _lock:
        if key in _initialized:
            if not allow_incomplete_migration:
                assert_migration_ready()
                _migration_checked.add(key)
            return
        check_connection()
        with pool().connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(74821401)")
            version = hashlib.sha256(SCHEMA_SQL.encode()).hexdigest()
            exists = conn.execute("SELECT to_regclass('public.schema_versions')").fetchone()[0]
            if not exists or not conn.execute('SELECT 1 FROM schema_versions WHERE version=%s',(version,)).fetchone():
                conn.execute(SCHEMA_SQL)
                conn.execute('INSERT INTO schema_versions(version) VALUES (%s)',(version,))
        if not allow_incomplete_migration:
            assert_migration_ready()
            _migration_checked.add(key)
        _initialized.add(key)


# ---------------------------------------------------------------------------
# Unit of Work (Transaction)
# ---------------------------------------------------------------------------

class UnitOfWork:
    """Transaction-scoped database operations, API-compatible with the MongoDB version."""

    def __init__(self, conn):
        self.conn = conn
        # Expose .db as self for code that does c.db.<collection> pattern
        self.db = _DirectAccess(conn)
        # Expose .session for code that passes session= kwargs (no-op in PG)
        self.session = self

    def begin(self):
        # PostgreSQL auto-begins transactions, nothing to do
        return Result()

    def aggregate(self, table, pipeline_or_sql, fields=None, default=None):
        """Execute a SQL query (replacing MongoDB aggregate pipelines).

        For backward compatibility, if pipeline_or_sql is a list (MongoDB pipeline),
        we convert the simple cases. But primarily this now receives raw SQL
        from callers that build dynamic queries.
        """
        if isinstance(pipeline_or_sql, list):
            rows = self.db[table].aggregate(pipeline_or_sql)
        else:
            cur = self.conn.execute(pipeline_or_sql)
            names = [d.name for d in cur.description]
            rows = [dict(zip(names, row)) for row in cur.fetchall()]
        if not rows and default is not None: rows = [default]
        return Result([decode_row(row, fields) for row in rows])

    def next_sequence(self, collection, scope='global'):
        """Atomic monotonic sequence using PostgreSQL's upsert + RETURNING."""
        seq_id = collection + ':' + str(scope)
        row = self.conn.execute(
            'INSERT INTO sequences (_id, value) VALUES (%s, 1) '
            'ON CONFLICT (_id) DO UPDATE SET value = sequences.value + 1 '
            'RETURNING value',
            (seq_id,),
        ).fetchone()
        return row[0]

    def insert(self, name, values, replace=False, keys=None, set_values=None, increments=None):
        """Insert a row, with optional upsert semantics."""
        values = encode_values(values)
        record = {**defaults(name), **values}

        # Auto-generate sequence IDs
        if name in ('audit', 'pos_events'):
            field = 'id' if name == 'audit' else 'sequence'
            if record[field] is None:
                record[field] = self.next_sequence(
                    name, record.get('store_id', record.get('workspace', 'global'))
                )
        if name == 'pos_inventory_movements':
            record['_sequence'] = self.next_sequence(name, record['store_id'])

        if set_values is not None or increments is not None:
            # Upsert with SET and/or INCREMENT
            keys = keys or primary_keys(name)
            updates = encode_values(set_values or {})
            inc = increments or {}
            record.update(updates)
            record.update(inc)

            cols = list(record.keys())
            vals = [_pg_value(k, record[k]) for k in cols]

            set_parts = []
            for k, v in updates.items():
                vals.append(_pg_value(k, v))
                set_parts.append(f'{k} = ${len(vals)}')
            for k, v in inc.items():
                vals.append(v)
                set_parts.append(f'{k} = {name}.{k} + ${len(vals)}')

            conflict_cols = ', '.join(keys)

            if set_parts:
                set_clause = ', '.join(set_parts)
                sql = (
                    f'INSERT INTO {name} ({", ".join(cols)}) '
                    f'VALUES ({", ".join(f"${i+1}" for i in range(len(cols)))}) '
                    f'ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clause}'
                )
            else:
                sql = (
                    f'INSERT INTO {name} ({", ".join(cols)}) '
                    f'VALUES ({", ".join(f"${i+1}" for i in range(len(cols)))}) '
                    f'ON CONFLICT ({conflict_cols}) DO NOTHING'
                )
            # psycopg uses %s placeholders, not $N - convert
            sql_psycopg = _dollar_to_percent(sql, len(vals))
            self.conn.execute(sql_psycopg, vals)

        elif replace:
            # Replace = upsert by primary key
            pk = primary_keys(name)
            cols = list(record.keys())
            vals = [_pg_value(k, record[k]) for k in cols]
            non_pk = [c for c in cols if c not in pk]
            conflict_cols = ', '.join(pk)

            if non_pk:
                update_clause = ', '.join(f'{c} = EXCLUDED.{c}' for c in non_pk)
                sql = (
                    f'INSERT INTO {name} ({", ".join(cols)}) '
                    f'VALUES ({", ".join("%s" for _ in cols)}) '
                    f'ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_clause}'
                )
            else:
                sql = (
                    f'INSERT INTO {name} ({", ".join(cols)}) '
                    f'VALUES ({", ".join("%s" for _ in cols)}) '
                    f'ON CONFLICT ({conflict_cols}) DO NOTHING'
                )
            self.conn.execute(sql, vals)
        else:
            # Simple insert
            cols = list(record.keys())
            vals = [_pg_value(k, record[k]) for k in cols]
            sql = (
                f'INSERT INTO {name} ({", ".join(cols)}) '
                f'VALUES ({", ".join("%s" for _ in cols)})'
            )
            self.conn.execute(sql, vals)

        return Result(rowcount=1)

    def run(self, name, params=()):
        # Preserve reviewed write semantics, including scoped sequence allocation.
        if name in WRITE_PLANS:
            spec = bind(WRITE_PLANS[name], params)
            table = spec['collection']; operation = spec['operation']
            if operation == 'insert':
                return self.insert(table, spec['values'], spec.get('replace', False))
            if operation == 'upsert':
                return self.insert(table, spec['values'], keys=spec['keys'],
                                   set_values=spec['set'], increments=spec['inc'])
            collection = self.db[table]
            if operation == 'delete':
                return Result(rowcount=collection.delete_many(spec['filter']).deleted_count)
            update = {}
            if spec['set']: update['$set'] = encode_values(spec['set'])
            if spec['inc']: update['$inc'] = spec['inc']
            return Result(rowcount=collection.update_many(spec['filter'], update).matched_count)
        spec = PLANS[name]
        sql, values = bind_sql(spec['sql'], params)
        cur = self.conn.execute(sql, values)
        fields = spec.get('fields') or [d.name for d in cur.description or ()]
        rows = [dict(zip(fields, row)) for row in cur.fetchall()] if fields else []
        if not rows and spec.get('default') is not None: rows = [spec['default']]
        return Result([decode_row(row, fields) for row in rows], rowcount=cur.rowcount)

    def run_many(self, name, rows):
        # Promotion-calendar inserts share the caller's atomic transaction, but
        # need no per-row result. Pipeline them instead of waiting per event.
        with self.conn.pipeline():
            for row in rows:
                self.run(name, row)
        return Result()


WRITE_PLANS = json.loads((Path(__file__).parent / 'pg_writes.json').read_text())


def bind(value, params):
    if isinstance(value, dict):
        if set(value) == {'$parameter'}: return params[value['$parameter']]
        if set(value) == {'$jsonParameter'}: return json.loads(params[value['$jsonParameter']])
        if set(value) == {'$likeParameter'}:
            return re.escape(str(params[value['$likeParameter']])).replace('%', '.*').replace('_', '.')
        return {k: bind(v, params) for k, v in value.items()}
    if isinstance(value, list): return [bind(v, params) for v in value]
    return value


def bind_sql(sql, params):
    # Parameters may be repeated or occur out of numerical order in converted plans.
    order = []
    def replace(match):
        index = int(match[1]) - 1
        if index >= len(params): raise ValueError('Missing named-query parameter')
        order.append(params[index]); return '%s'
    return re.sub(r'\$(\d+)', replace, sql.replace('%', '%%')), order


def _dollar_to_percent(sql, n_params):
    return re.sub(r'\$\d+', '%s', sql)


def _pipeline_to_sql(table, pipeline):
    """Convert simple MongoDB aggregation pipelines to SQL.

    Handles the common pattern: [{$match: ...}, {$sort: ...}, {$project: ...}]
    Used only for the few places that build pipelines dynamically (e.g., promotions.py).
    """
    where_parts = []
    order_parts = []
    select_cols = ['*']
    params = []

    for stage in pipeline:
        if '$match' in stage:
            match = stage['$match']
            for key, val in match.items():
                if isinstance(val, dict):
                    for op, operand in val.items():
                        if op == '$eq':
                            where_parts.append(f'{key} = %s')
                            params.append(operand)
                        elif op == '$ne':
                            where_parts.append(f'{key} != %s')
                            params.append(operand)
                        elif op == '$gt':
                            where_parts.append(f'{key} > %s')
                            params.append(operand)
                        elif op == '$gte':
                            where_parts.append(f'{key} >= %s')
                            params.append(operand)
                        elif op == '$lt':
                            where_parts.append(f'{key} < %s')
                            params.append(operand)
                        elif op == '$lte':
                            where_parts.append(f'{key} <= %s')
                            params.append(operand)
                        elif op == '$in':
                            placeholders = ', '.join(['%s'] * len(operand))
                            where_parts.append(f'{key} IN ({placeholders})')
                            params.extend(operand)
                        elif op == '$nin':
                            placeholders = ', '.join(['%s'] * len(operand))
                            where_parts.append(f'{key} NOT IN ({placeholders})')
                            params.extend(operand)
                else:
                    where_parts.append(f'{key} = %s')
                    params.append(val)

        elif '$sort' in stage:
            for key, direction in stage['$sort'].items():
                order_parts.append(f'{key} {"ASC" if direction == 1 else "DESC"}')

        elif '$project' in stage:
            cols = [k for k, v in stage['$project'].items() if k != '_id' and v]
            if cols:
                select_cols = cols

    sql = f'SELECT {", ".join(select_cols)} FROM {table}'
    if where_parts:
        sql += ' WHERE ' + ' AND '.join(where_parts)
    if order_parts:
        sql += ' ORDER BY ' + ', '.join(order_parts)

    return sql, tuple(params)


from .pg_documents import Database as _DirectAccess


def database():
    """A lightweight handle. Connections are borrowed only for a bounded operation."""
    return _DirectAccess()


@contextmanager
def operation_connection(session=None, bound=None, *, read_only=False):
    conn = getattr(session, 'conn', None) or bound or _active_connection.get()
    if conn is not None:
        yield conn
    else:
        with pool().connection() as conn:
            if read_only:
                # A single SELECT already has an atomic PostgreSQL snapshot.
                # Avoid BEGIN/COMMIT round trips for standalone reads; explicit
                # units of work above retain their serializable transaction.
                conn.autocommit = True
                try:
                    yield conn
                finally:
                    conn.autocommit = False
            else:
                yield conn


@contextmanager
def connection():
    initialize()
    # Nested units share the parent's transaction and cannot commit it early.
    if _active_connection.get() is not None:
        yield UnitOfWork(_active_connection.get()); return
    with pool().connection() as conn:
        token = _active_connection.set(conn)
        try:
            yield UnitOfWork(conn)
        finally:
            _active_connection.reset(token)


def retry_transaction(function):
    """Retry the entire business operation after a serialization failure."""
    from functools import wraps
    import random

    @wraps(function)
    def wrapped(*args, **kwargs):
        for attempt in range(4):
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                # Check for PostgreSQL serialization failures (40001) and deadlocks (40P01)
                error_code = getattr(exc, 'sqlstate', None) or ''
                is_retryable = error_code in ('40001', '40P01')
                # Also check for unique violation (23505) like the MongoDB DuplicateKeyError handling
                is_duplicate = error_code == '23505'
                if not (is_retryable or is_duplicate) or attempt == 3:
                    raise
                time.sleep((.02 * 2 ** attempt) + random.random() * .02)
    return wrapped


# ---------------------------------------------------------------------------
# Custom APIRoute with Transaction Retry
# ---------------------------------------------------------------------------

from fastapi.routing import APIRoute
import inspect


class PgRoute(APIRoute):
    """APIRoute that auto-wraps synchronous endpoints with transaction retry."""
    def __init__(self, path, endpoint, **kwargs):
        if not inspect.iscoroutinefunction(endpoint) and not getattr(endpoint, '_pg_retry', False):
            signature = inspect.signature(endpoint, eval_str=True)
            endpoint = retry_transaction(endpoint)
            endpoint.__signature__ = signature
            endpoint._pg_retry = True
        super().__init__(path, endpoint, **kwargs)


# Alias for backward compatibility
MongoRoute = PgRoute
