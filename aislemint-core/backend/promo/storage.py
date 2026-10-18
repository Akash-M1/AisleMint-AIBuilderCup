"""Versioned PostgreSQL assets with atomic manifests and checksum verification.

AssetPath is a logical key, never a local directory. Model inference reads into
memory; no disk cache or fallback is used. Local paths are accepted only for
explicit offline test fixtures and the one-time migration reader.

Replaces the former GridFS-backed implementation; binary blobs are now stored in
the `asset_blobs` table as BYTEA with the same gzip compression and SHA-256
verification. All callers continue to use `AssetPath` and `publish()` unchanged.
"""
from __future__ import annotations
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
import io,json,hashlib,uuid,gzip,zlib
import pandas as pd
import joblib

def _db():
    from .pg import database
    return database()

def _ROOT():
    from .runtime import ROOT
    return ROOT

from collections import OrderedDict
import time as _time
import threading as _threading

_CACHE_LOCK = _threading.Lock()
_ASSET_DATA_CACHE: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_ASSET_EXISTS_CACHE: dict[str, tuple[float, bool]] = {}
_ASSET_TTL = 45.0
_MAX_CACHED_ASSETS = 64

def _invalidate_asset_cache(key: str | None = None):
    with _CACHE_LOCK:
        if key is None:
            _ASSET_DATA_CACHE.clear()
            _ASSET_EXISTS_CACHE.clear()
        else:
            _ASSET_DATA_CACHE.pop(key, None)
            _ASSET_EXISTS_CACHE.clear()

class AssetPath:
    def __init__(self,key):
        self.key=str(key.key if isinstance(key,AssetPath) else key).strip('/')
        if '..' in PurePosixPath(self.key).parts:raise ValueError('Asset keys cannot traverse namespaces.')
    def __truediv__(self,part):return AssetPath(str(PurePosixPath(self.key)/str(part)))
    def __str__(self):return 'pg://assets/'+self.key
    def __repr__(self):return f'AssetPath({self.key!r})'
    def __hash__(self):return hash(self.key)
    def __eq__(self,other):return isinstance(other,AssetPath) and self.key==other.key
    @property
    def name(self):return PurePosixPath(self.key).name
    @property
    def parent(self):return AssetPath(str(PurePosixPath(self.key).parent).removeprefix('.'))
    @property
    def suffix(self):return PurePosixPath(self.key).suffix
    @property
    def stem(self):return PurePosixPath(self.key).stem
    def with_name(self,name):return self.parent/name
    def resolve(self):return self
    def exists(self):
        now_t = _time.monotonic()
        with _CACHE_LOCK:
            hit = _ASSET_EXISTS_CACHE.get(f'e:{self.key}')
            if hit and (now_t - hit[0]) < _ASSET_TTL:
                return hit[1]
        from .pg import operation_connection
        with operation_connection(read_only=True) as conn:
            import re as _re
            row=conn.execute(
                "SELECT 1 FROM assets WHERE key ~ %s LIMIT 1",
                ('^'+_re.escape(self.key)+'(?:/|$)',)
            ).fetchone()
            res = row is not None
        with _CACHE_LOCK:
            _ASSET_EXISTS_CACHE[f'e:{self.key}'] = (now_t, res)
        return res
    def is_file(self):
        now_t = _time.monotonic()
        with _CACHE_LOCK:
            hit = _ASSET_EXISTS_CACHE.get(f'f:{self.key}')
            if hit and (now_t - hit[0]) < _ASSET_TTL:
                return hit[1]
        from .pg import operation_connection
        with operation_connection(read_only=True) as conn:
            row=conn.execute("SELECT 1 FROM assets WHERE key = %s AND kind = 'file' LIMIT 1",(self.key,)).fetchone()
            res = row is not None
        with _CACHE_LOCK:
            _ASSET_EXISTS_CACHE[f'f:{self.key}'] = (now_t, res)
        return res
    def mkdir(self,parents=False,exist_ok=False):
        if not exist_ok and self.exists():raise FileExistsError(str(self))
        from .pg import operation_connection
        with operation_connection() as conn:
            conn.execute(
                "INSERT INTO assets (key, kind, created) VALUES (%s, 'directory', %s) "
                "ON CONFLICT (key) DO NOTHING",
                (self.key,datetime.now(timezone.utc).isoformat())
            )
        _invalidate_asset_cache(self.key)
    def read_bytes(self):
        now_t = _time.monotonic()
        with _CACHE_LOCK:
            hit = _ASSET_DATA_CACHE.get(self.key)
            if hit and (now_t - hit[0]) < _ASSET_TTL:
                _ASSET_DATA_CACHE.move_to_end(self.key)
                return hit[1]
        from .pg import operation_connection
        with operation_connection(read_only=True) as conn:
            row=conn.execute(
                "SELECT a.compression,a.sha256,b.data FROM assets a "
                "LEFT JOIN asset_blobs b ON b.blob_id=a.blob_id "
                "WHERE a.key=%s AND a.kind='file'",(self.key,)
            ).fetchone()
        if row is None:raise FileNotFoundError(str(self))
        compression,checksum,blob=row
        if blob is None:raise FileNotFoundError(f'Blob not found for {self.key}')
        # Release standalone connections before CPU-heavy decompression/checks.
        data=bytes(blob)
        if compression=='gzip':data=gzip.decompress(data)
        if hashlib.sha256(data).hexdigest()!=checksum:raise ValueError('Asset checksum mismatch: '+self.key)
        if len(data) <= 32 * 1024 * 1024:
            with _CACHE_LOCK:
                _ASSET_DATA_CACHE[self.key] = (now_t, data)
                _ASSET_DATA_CACHE.move_to_end(self.key)
                while len(_ASSET_DATA_CACHE) > _MAX_CACHED_ASSETS:
                    _ASSET_DATA_CACHE.popitem(last=False)
                _ASSET_EXISTS_CACHE[f'e:{self.key}'] = (now_t, True)
                _ASSET_EXISTS_CACHE[f'f:{self.key}'] = (now_t, True)
        return data
    def read_text(self,encoding='utf-8'):return self.read_bytes().decode(encoding)
    def write_bytes(self,data):
        publish(self.key,io.BytesIO(data));return len(data)
    def write_text(self,text,encoding='utf-8'):return self.write_bytes(text.encode(encoding))
    def open(self,mode='r',encoding='utf-8'):
        if mode in ('r','rb'):return io.BytesIO(self.read_bytes()) if 'b' in mode else io.StringIO(self.read_text(encoding))
        if mode in ('w','wb'):return AssetWriter(self,binary='b' in mode)
        raise ValueError('Only immutable read/write asset streams are supported.')
    def replace(self,target):
        target=managed(target)
        if not isinstance(target,AssetPath):raise ValueError('Cannot move a database asset onto disk.')
        from .pg import operation_connection
        with operation_connection() as conn:
            cur=conn.execute("SELECT * FROM assets WHERE key = %s",(self.key,));meta=cur.fetchone()
            if not meta:raise FileNotFoundError(str(self))
            desc=[d.name for d in cur.description]
            doc=dict(zip(desc,meta))
            doc['key']=target.key
            # Upsert the target
            conn.execute(
                "INSERT INTO assets (key, kind, blob_id, sha256, size, compression, created) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET kind=EXCLUDED.kind, blob_id=EXCLUDED.blob_id, "
                "sha256=EXCLUDED.sha256, size=EXCLUDED.size, compression=EXCLUDED.compression, created=EXCLUDED.created",
                (target.key, doc['kind'], doc.get('blob_id'), doc.get('sha256'), doc.get('size'), doc.get('compression'), doc['created'])
            )
            conn.execute("DELETE FROM assets WHERE key = %s",(self.key,))
            _invalidate_asset_cache(self.key)
            _invalidate_asset_cache(target.key)
            return target
    def unlink(self,missing_ok=False):
        from .pg import operation_connection
        with operation_connection() as conn:
            cur=conn.execute("DELETE FROM assets WHERE key = %s",(self.key,))
            _invalidate_asset_cache(self.key)
            if cur.rowcount == 0 and not missing_ok:raise FileNotFoundError(str(self))

class AssetWriter(io.BytesIO):
    def __init__(self,path,binary=True):super().__init__();self.path=path;self.binary=binary
    def write(self,data):return super().write(data if isinstance(data,bytes) else data.encode())
    def __exit__(self,kind,value,tb):
        if kind is None:self.path.write_bytes(self.getvalue())
        self.close()

class HashingReader:
    def __init__(self,stream):self.stream=stream;self.hash=hashlib.sha256();self.size=0
    def read(self,n=-1):
        data=self.stream.read(n);self.hash.update(data);self.size+=len(data);return data

class GzipReader:
    def __init__(self,reader):self.reader=reader;self.compressor=zlib.compressobj(level=6,wbits=31);self.buffer=b'';self.ended=False
    def read(self,n=-1):
        while not self.ended and (n<0 or len(self.buffer)<n):
            block=self.reader.read(1024*1024)
            if block:self.buffer+=self.compressor.compress(block)
            else:self.buffer+=self.compressor.flush();self.ended=True
        if n<0:data=self.buffer;self.buffer=b'';return data
        data=self.buffer[:n];self.buffer=self.buffer[n:];return data

def publish(key,stream):
    from .jobs import training_lease
    training_lease.assert_owned()
    from .pg import operation_connection
    with operation_connection() as conn:
        reader=HashingReader(stream)
        # Compress and read all data
        compressed=GzipReader(reader).read()
        blob_id=uuid.uuid4().hex
        # Store compressed blob in asset_blobs table
        conn.execute(
            "INSERT INTO asset_blobs (blob_id, asset_key, data, created) VALUES (%s, %s, %s, %s)",
            (blob_id, key, compressed, datetime.now(timezone.utc))
        )
        record={'key':key,'kind':'file','blob_id':blob_id,'sha256':reader.hash.hexdigest(),'size':reader.size,'compression':'gzip','created':datetime.now(timezone.utc).isoformat()}
        # Insert version history and upsert current manifest
        conn.execute(
            "INSERT INTO asset_versions (key, kind, blob_id, sha256, size, compression, created) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (key, 'file', blob_id, record['sha256'], record['size'], 'gzip', record['created'])
        )
        conn.execute(
            "INSERT INTO assets (key, kind, blob_id, sha256, size, compression, created) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET kind=EXCLUDED.kind, blob_id=EXCLUDED.blob_id, "
            "sha256=EXCLUDED.sha256, size=EXCLUDED.size, compression=EXCLUDED.compression, created=EXCLUDED.created",
            (key, 'file', blob_id, record['sha256'], record['size'], 'gzip', record['created'])
        )
        _invalidate_asset_cache(key)
        return record

def managed(path):
    if isinstance(path,AssetPath):return path
    text=str(path)
    if text.startswith('mongo://assets/') or text.startswith('pg://assets/'):
        prefix='mongo://assets/' if text.startswith('mongo://') else 'pg://assets/'
        return AssetPath(text.removeprefix(prefix))
    p=Path(path)
    ROOT=_ROOT()
    # Logical asset keys must not become local paths when commands run from Ops
    # or another repository instead of the former organization root.
    if not p.is_absolute() and p.parts and p.parts[0] in ('data','artifacts'):
        return AssetPath(p.as_posix())
    try:relative=p.resolve().relative_to(ROOT)
    except ValueError:return p
    if relative.parts and relative.parts[0] in ('data','artifacts'):return AssetPath(relative.as_posix())
    return p

def read_frame(path,**kwargs):
    p=managed(path);return pd.read_parquet(io.BytesIO(p.read_bytes()) if isinstance(p,AssetPath) else p,**kwargs)
def write_frame(frame,path,**kwargs):
    p=managed(path)
    if isinstance(p,AssetPath):
        stream=io.BytesIO();frame.to_parquet(stream,**kwargs);p.write_bytes(stream.getvalue())
    else:frame.to_parquet(p,**kwargs)
def load_model(path):
    p=managed(path);return joblib.load(io.BytesIO(p.read_bytes()) if isinstance(p,AssetPath) else p)
def dump_model(model,path):
    p=managed(path)
    if isinstance(p,AssetPath):
        stream=io.BytesIO();joblib.dump(model,stream);p.write_bytes(stream.getvalue())
    else:joblib.dump(model,p)
def read_csv_reference(path,**kwargs):
    p=managed(path);return pd.read_csv(io.BytesIO(p.read_bytes()) if isinstance(p,AssetPath) else p,**kwargs)
