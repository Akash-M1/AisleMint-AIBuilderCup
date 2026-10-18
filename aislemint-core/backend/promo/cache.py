"""Optional, bounded Redis cache for derived, versioned business responses.

Authorization and current release pointers are checked by callers on every read.
Transactions never populate the cache. PostgreSQL remains usable during outages.
"""
from functools import lru_cache
import hashlib
import json
import os
import random
import threading
import time

_failed_until = 0.0
_locks = [threading.Lock() for _ in range(64)]
MAX_VALUE_BYTES = 256 * 1024


def health():
    """Expose cache availability separately from the authoritative DB health."""
    cache=client()
    if cache is None:return 'disabled'
    from redis.exceptions import RedisError
    try:return 'ready' if cache.ping() else 'unavailable'
    except RedisError:return 'unavailable'


@lru_cache(maxsize=1)
def client():
    host = os.environ.get('REDIS_HOST')
    if not host:
        return None
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    tls = os.environ.get('REDIS_TLS', 'true').lower() == 'true'
    if not tls and host not in ('localhost', '127.0.0.1'):
        raise ValueError('Remote Redis connections require TLS.')
    options = dict(host=host, port=int(os.environ.get('REDIS_PORT', '6378')),
        password=os.environ.get('REDIS_PASSWORD'), db=int(os.environ.get('REDIS_DB', '0')),
        socket_connect_timeout=.3, socket_timeout=.3, max_connections=16,
        retry=Retry(NoBackoff(), 0), decode_responses=False)
    if tls:
        options.update(ssl=True, ssl_cert_reqs='required', ssl_check_hostname=True,
                       ssl_ca_certs=os.environ['REDIS_CA_CERTS'])
    return redis.Redis(**options)


def remember(kind, workspace, version, load, ttl=300):
    from .pg import _active_connection
    global _failed_until
    if _active_connection.get() is not None or time.monotonic() < _failed_until:
        return load()
    cache = client()
    if cache is None:
        return load()
    from redis.exceptions import RedisError
    digest = hashlib.sha256(json.dumps([kind, workspace, version], sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    key = 'aislemint:cache:v1:' + kind + ':' + digest

    def read():
        value = cache.get(key)
        if value is None or len(value) > MAX_VALUE_BYTES:
            return None
        try:
            envelope = json.loads(value)
            if isinstance(envelope, dict) and envelope.get('format') == 1:
                return envelope
        except (ValueError, UnicodeError):
            pass
        return None

    try:
        hit = read()
        if hit is not None:
            return hit['value']
    except (RedisError, KeyError):
        _failed_until = time.monotonic() + 30
        return load()

    # Coalesce same-key cold reads within one instance without unbounded locks.
    with _locks[int(digest[:8], 16) % len(_locks)]:
        try:
            hit = read()
            if hit is not None:
                return hit['value']
        except (RedisError, KeyError):
            _failed_until = time.monotonic() + 30
            return load()
        result = load()
        try:
            payload = json.dumps({'format': 1, 'value': result},
                separators=(',', ':'), allow_nan=False).encode()
            if len(payload) <= MAX_VALUE_BYTES:
                cache.set(key, payload, ex=max(1, ttl + random.randint(-ttl // 10, ttl // 10)))
        except RedisError:
            _failed_until = time.monotonic() + 30
        except (TypeError, ValueError):
            pass # Return fresh results even when they cannot be JSON-cached.
        return result
