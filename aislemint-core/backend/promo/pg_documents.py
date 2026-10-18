"""Bounded PostgreSQL access for document-shaped business records.

Filters compile to parameterized SQL. A unit of work owns every related write;
standalone operations borrow and return one connection. Unsupported operators
fail closed instead of silently widening tenant or worker filters.
"""
from contextlib import contextmanager
from datetime import datetime, date, timezone
from functools import wraps
from types import SimpleNamespace
import copy
import json
import re
import uuid
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row

DOCUMENT_TABLES = frozenset('retailers retail_shops retail_users retail_sessions retail_keys retail_snapshots retail_batches retail_runs retail_ledger_jobs retail_audit retail_login_attempts connect_sources connect_types connect_type_versions connect_runs connect_batches connect_objects connect_versions connect_quarantine connect_decisions connect_questions connect_demo_manifests data_products data_jobs data_releases data_chunks integration_requests'.split())


def ident(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError('Invalid persistence identifier')
    return '"' + value + '"'


def encode(value):
    if isinstance(value, datetime):
        return {'__aislemint_datetime__': value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()}
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, dict): return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'__aislemint_datetime__'}: return datetime.fromisoformat(value['__aislemint_datetime__'])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list): return [decode(v) for v in value]
    return value


def json_value(value): return Jsonb(encode(value))


def column(key, document=False):
    if key == '_id': return ident(key)
    if not document and '.' not in key: return ident(key)
    parts = key.split('.')
    # SQL identifiers/paths originate in reviewed code, but validate dynamic tenant keys too.
    for part in parts:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',part):raise ValueError('Invalid persistence path')
    base='data' if document else ident(parts.pop(0))
    return base + ''.join("->'" + part + "'" for part in parts)


def where(selector, document=False):
    terms = []; values = []
    def atom(key, op, value):
        # Ontology edges are arrays of objects; -> cannot traverse that array.
        if document and key == 'references.target':
            if op not in ('$eq','$in'):raise ValueError('Unsupported relationship filter')
            targets=value if op=='$in' else [value]
            values.extend(json_value([{'target':target}]) for target in targets)
            return '('+' OR '.join("data->'references' @> %s" for _ in targets)+')' if targets else 'FALSE'
        col = column(key, document); is_json = (document and key != '_id') or '.' in key
        param = json_value(value) if is_json else value
        if op in ('$eq', '$ne'):
            if value is None:
                expr = f'({col} IS NULL' + (f" OR {col} = 'null'::jsonb" if is_json else '') + ')'
            elif is_json:
                # Mongo-style equality also matches an element of an array field.
                expr = f'({col} = %s OR (jsonb_typeof({col}) = \'array\' AND {col} @> %s))'
                values.extend([param, json_value([value])])
            else:
                expr = f'{col} = %s'; values.append(param)
            return f'({col} IS NULL OR NOT ({expr}))' if op == '$ne' and value is not None else f'NOT ({expr})' if op == '$ne' else expr
        if op in ('$gt', '$gte', '$lt', '$lte'):
            values.append(param); return f'{col} ' + {'$gt':'>','$gte':'>=','$lt':'<','$lte':'<='}[op] + ' %s'
        if op in ('$in', '$nin'):
            clauses = [atom(key, '$eq', v) for v in value]
            expr = '(' + ' OR '.join(clauses) + ')' if clauses else 'FALSE'
            return f'({col} IS NULL OR NOT {expr})' if op == '$nin' else expr
        if op == '$exists': return f'{col} IS ' + ('NOT NULL' if value else 'NULL')
        if op == '$type':
            if not is_json: return f'{col} IS NOT NULL'
            types = {'string':'string','number':'number','object':'object','array':'array','bool':'boolean'}
            if value not in types: raise ValueError('Unsupported persistence type filter')
            values.append(types[value]); return f'jsonb_typeof({col}) = %s'
        if op == '$regex':
            values.append(value); return f'({col} #>> \'{{}}\') ~ %s' if is_json else f'{col} ~ %s'
        if op == '$not':
            return 'NOT (' + ' AND '.join(atom(key, sub, operand) for sub, operand in value.items()) + ')'
        raise ValueError('Unsupported persistence filter: ' + op)
    def expression(value):
        if isinstance(value,dict) and set(value)=={'$add'}: return '('+' + '.join(expression(v) for v in value['$add'])+')'
        if isinstance(value,dict) and set(value)=={'$not'}: return 'NOT ('+expression(value['$not'])+')'
        if isinstance(value,dict) and set(value)=={'$in'}:
            left,items=value['$in'];return '('+ ' OR '.join(expression(left)+'='+expression(item) for item in items)+')'
        if isinstance(value,str) and value.startswith('$'): return column(value[1:], document)
        if isinstance(value,dict) and set(value)=={'$toLower'}:
            ref=expression(value['$toLower']); return f'to_jsonb(lower({ref} #>> \'{{}}\'))'
        values.append(json_value(value) if document else value); return '%s'
    for key, value in (selector or {}).items():
        if key in ('$or', '$and'):
            subterms=[]
            for sub in value:
                term, params = where(sub, document); subterms.append('(' + term + ')'); values.extend(params)
            terms.append((' OR ' if key == '$or' else ' AND ').join(subterms) or ('FALSE' if key == '$or' else 'TRUE'))
        elif key == '$expr':
            if len(value)!=1: raise ValueError('Invalid persistence expression')
            op,args=next(iter(value.items())); operators={'$gt':'>','$gte':'>=','$lt':'<','$lte':'<=','$eq':'=','$ne':'<>'}
            if op in ('$not','$in'):terms.append(expression(value))
            elif op in operators:terms.append(expression(args[0])+' '+operators[op]+' '+expression(args[1]))
            else:raise ValueError('Unsupported persistence expression')
        elif key.startswith('$'): raise ValueError('Unsupported persistence selector')
        elif isinstance(value,dict) and any(k.startswith('$') for k in value):
            for op, operand in value.items():
                if op=='$options': continue
                if op=='$regex' and 'i' in value.get('$options',''): operand='(?i)'+operand
                terms.append(atom(key,op,operand))
        else: terms.append(atom(key,'$eq',value))
    return ' AND '.join('('+term+')' for term in terms) or 'TRUE', values


def order_by(sort, document=False):
    if not sort: return ''
    if isinstance(sort,dict): sort=list(sort.items())
    return ' ORDER BY ' + ', '.join(column(k,document)+(' ASC' if d==1 else ' DESC')+' NULLS LAST' for k,d in sort)


def projection(doc, fields):
    if not fields: return doc
    included=[k for k,v in fields.items() if v and k!='_id']
    if included or fields == {'_id':1}:
        result={k:doc[k] for k in included if k in doc}
        if fields.get('_id',1) and '_id' in doc: result['_id']=doc['_id']
        return result
    return {k:v for k,v in doc.items() if fields.get(k,1)}


def scoped(method=None, *, read_only=False):
    if method is None:
        return lambda method: scoped(method, read_only=read_only)
    @wraps(method)
    def wrapped(self,*args,session=None,**kwargs):
        from .pg import operation_connection
        with operation_connection(session,self.conn,read_only=read_only) as conn:
            return method(Collection(conn,self.name),*args,**kwargs)
    return wrapped


class Database:
    def __init__(self,conn=None): self.conn=conn
    def __getattr__(self,name):
        if name.startswith('_'): raise AttributeError(name)
        return self[name]
    def __getitem__(self,name): return Collection(self.conn,name)
    def list_collection_names(self):
        from .pg import operation_connection
        with operation_connection(bound=self.conn,read_only=True) as conn:
            return [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
    def command(self, command):
        if command!='ping': raise ValueError('Unsupported database command')
        from .pg import check_connection
        return {'ok':bool(check_connection())}


class Collection:
    def __init__(self,conn,name):
        self.conn=conn; self.name=name; self.table=ident(name); self.document=name in DOCUMENT_TABLES
    def unpack(self,row): return {'_id':row['_id'],**decode(row['data'])} if self.document else dict(row)
    def select(self,selector=None,sort=None,limit=None,offset=0,lock=False,fields=None):
        predicate,params=where(selector,self.document)
        columns='*'
        if self.document and fields and all('.' not in k for k in fields):
            included=[k for k,v in fields.items() if v and k!='_id']
            if included or fields == {'_id':1}:
                columns="_id, (SELECT coalesce(jsonb_object_agg(k,data->k),'{}'::jsonb) FROM unnest(%s::text[]) AS f(k) WHERE data ? k) AS data"
                params=[included,*params]
            else:
                columns='_id, data - %s::text[] AS data'
                params=[[k for k,v in fields.items() if not v and k!='_id'],*params]
        sql=f'SELECT {columns} FROM {self.table} WHERE {predicate}'+order_by(sort,self.document)
        if limit: sql+=' LIMIT %s';params.append(limit)
        if offset: sql+=' OFFSET %s';params.append(offset)
        if lock: sql+=' FOR UPDATE'
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql,params); return [self.unpack(r) for r in cur.fetchall()]
    @scoped(read_only=True)
    def find_one(self,filter=None,projection=None,sort=None):
        rows=self.select(filter,sort,1,fields=projection);return globals()['projection'](rows[0],projection) if rows else None
    def find(self,filter=None,projection=None,session=None): return Cursor(self,filter,projection,session)
    @scoped
    def insert_one(self,document):
        doc=copy.deepcopy(document)
        if self.document:
            id=str(doc.pop('_id',None) or doc.get('id') or uuid.uuid4().hex)
            self.conn.execute(f'INSERT INTO {self.table} (_id,data) VALUES (%s,%s)',(id,json_value(doc)))
        else:
            from .pg import _pg_value, SCHEMA
            id=doc.get('_id',doc.get('id'))
            for field in SCHEMA.get(self.name,{}).get('fields',[]):
                value=doc.get(field['name'])
                if field['type']=='INTEGER' and value is not None and (not isinstance(value,int) or isinstance(value,bool)):
                    raise ValueError('Integer required for '+field['name'])
            keys=list(doc)
            self.conn.execute(f'INSERT INTO {self.table} ('+', '.join(map(ident,keys))+') VALUES ('+', '.join(['%s']*len(keys))+')',[_pg_value(k,doc[k]) for k in keys])
        return SimpleNamespace(inserted_id=id)
    @scoped
    def insert_many(self,documents):
        if not self.document:
            return SimpleNamespace(inserted_ids=[self.insert_one(d).inserted_id for d in documents])
        ids=[]; rows=[]
        for document in documents:
            doc=copy.deepcopy(document)
            id=str(doc.pop('_id',None) or doc.get('id') or uuid.uuid4().hex)
            ids.append(id);rows.append((id,json_value(doc)))
        if rows:
            # psycopg executemany pipelines these inserts over one transaction.
            with self.conn.cursor() as cur:
                cur.executemany(f'INSERT INTO {self.table} (_id,data) VALUES (%s,%s)',rows)
        return SimpleNamespace(inserted_ids=ids)
    def _replace(self,old,new):
        if self.document:
            id=old['_id'];data={k:v for k,v in new.items() if k!='_id'}
            self.conn.execute(f'UPDATE {self.table} SET data=%s WHERE _id=%s',(json_value(data),id))
        else:
            from .pg import primary_keys, _pg_value, SCHEMA
            pk=primary_keys(self.name)
            if pk==['_id'] and '_id' not in old:
                pk=['key'] if self.name=='assets' else ['store_id','staff_id'] if self.name=='operation_recovery' else pk
            predicate,params=where({k:old[k] for k in pk})
            keys=[k for k in new if k not in pk]
            for field in SCHEMA.get(self.name,{}).get('fields',[]):
                value=new.get(field['name'])
                if field['type']=='INTEGER' and value is not None and (not isinstance(value,int) or isinstance(value,bool)):
                    raise ValueError('Integer required for '+field['name'])
            self.conn.execute(f'UPDATE {self.table} SET '+', '.join(ident(k)+'=%s' for k in keys)+f' WHERE {predicate}',[_pg_value(k,new[k]) for k in keys]+params)
    @scoped
    def replace_one(self,filter,replacement,upsert=False):
        rows=self.select(filter,limit=1,lock=True)
        if rows: self._replace(rows[0],replacement)
        elif upsert: self.insert_one({**filter,**replacement})
        return SimpleNamespace(matched_count=len(rows),modified_count=len(rows))
    def _update(self,filter,update,upsert=False,many=False,sort=None,after=False):
        rows=self.select(filter,sort,None if many else 1,lock=True)
        if not rows and upsert:
            doc={k:v for k,v in filter.items() if not k.startswith('$') and not isinstance(v,dict)}
            doc=apply_update(doc,update,inserting=True)
            result=self.insert_one(doc)
            return SimpleNamespace(matched_count=0,modified_count=0,upserted_id=result.inserted_id),{**doc,'_id':result.inserted_id} if after else None
        new=None
        for old in rows:
            new=apply_update(old,update); self._replace(old,new)
        return SimpleNamespace(matched_count=len(rows),modified_count=len(rows),upserted_id=None),(new if after else rows[0]) if rows else None
    @scoped
    def update_one(self,filter,update,upsert=False): return self._update(filter,update,upsert)[0]
    @scoped
    def update_many(self,filter,update): return self._update(filter,update,many=True)[0]
    @scoped
    def find_one_and_update(self,filter,update,upsert=False,sort=None,return_document=False): return self._update(filter,update,upsert,sort=sort,after=bool(return_document))[1]
    @scoped
    def delete_one(self,filter): return self._delete(filter,one=True)
    @scoped
    def delete_many(self,filter): return self._delete(filter)
    def _delete(self,filter,one=False):
        predicate,params=where(filter,self.document)
        if one: predicate=f'ctid IN (SELECT ctid FROM {self.table} WHERE {predicate} LIMIT 1)'
        cur=self.conn.execute(f'DELETE FROM {self.table} WHERE {predicate}',params)
        return SimpleNamespace(deleted_count=cur.rowcount)
    @scoped(read_only=True)
    def count_documents(self,filter):
        predicate,params=where(filter,self.document)
        return self.conn.execute(f'SELECT count(*) FROM {self.table} WHERE {predicate}',params).fetchone()[0]
    @scoped
    def bulk_write(self,operations,ordered=True):
        from pymongo import ReplaceOne,UpdateOne
        pending=[]; pending_sql=None
        def flush():
            if pending:
                with self.conn.cursor() as cur:cur.executemany(pending_sql,pending)
                pending.clear()
        for op in operations:
            sql=None;params=None
            # Preserve conditional/nested update semantics through the general
            # implementation. The connector's exact-ID replacements and simple
            # observation stamps can use atomic SQL and pipelined batches.
            exact_id=(self.document and isinstance(op,(ReplaceOne,UpdateOne))
                      and set(op._filter)=={'_id'} and isinstance(op._filter['_id'],str))
            if exact_id and isinstance(op,ReplaceOne) and op._upsert and op._doc.get('_id',op._filter['_id'])==op._filter['_id']:
                doc={k:v for k,v in op._doc.items() if k!='_id'}
                sql=f'INSERT INTO {self.table} (_id,data) VALUES (%s,%s) ON CONFLICT (_id) DO UPDATE SET data=EXCLUDED.data'
                params=(op._filter['_id'],json_value(doc))
            elif exact_id and isinstance(op,UpdateOne) and not op._upsert and set(op._doc)=={'$set'} and all('.' not in k and k!='_id' for k in op._doc['$set']):
                sql=f'UPDATE {self.table} SET data=data || %s WHERE _id=%s'
                params=(json_value(op._doc['$set']),op._filter['_id'])
            if sql is not None:
                if pending_sql!=sql:flush();pending_sql=sql
                pending.append(params)
            else:
                flush();pending_sql=None
                if isinstance(op,ReplaceOne):self.replace_one(op._filter,op._doc,upsert=op._upsert)
                elif isinstance(op,UpdateOne):self.update_one(op._filter,op._doc,upsert=op._upsert)
                else:raise ValueError('Unsupported bulk operation')
        flush()
    @scoped(read_only=True)
    def aggregate(self,pipeline,maxTimeMS=None):
        # Only the reviewed dynamic aggregations are supported. Others need named SQL.
        match={};sort=None;group=None
        for stage in pipeline:
            if '$match' in stage:match=stage['$match']
            elif '$sort' in stage:sort=stage['$sort']
            elif '$group' in stage:group=stage['$group']
            else:raise ValueError('Use a named SQL plan for this aggregation stage')
        predicate,params=where(match,self.document)
        if not group:return self.select(match,sort)
        key=column(group['_id'].removeprefix('$'),self.document)
        output=[f'{key} AS "_id"'];distinct=False
        for alias,expr in group.items():
            if alias=='_id':continue
            if expr=={'$sum':1}:output.append('count(*) AS '+ident(alias))
            elif '$first' in expr:
                distinct=True;output.append(column(expr['$first'].removeprefix('$'),self.document)+' AS '+ident(alias))
            else:raise ValueError('Unsupported aggregation accumulator')
        sql='SELECT '+('DISTINCT ON ('+key+') ' if distinct else '')+', '.join(output)+f' FROM {self.table} WHERE {predicate}'
        sql+=(order_by([(group['_id'][1:],1),*list((sort or {}).items())],self.document) if distinct else ' GROUP BY '+key)
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql,params);return [dict(row) for row in cur.fetchall()]


def apply_update(original,update,inserting=False):
    doc=copy.deepcopy(original)
    for op,values in update.items():
        if op=='$setOnInsert' and not inserting:continue
        if op not in ('$set','$setOnInsert','$unset','$inc','$push'):raise ValueError('Unsupported persistence update '+op)
        for path,value in values.items():
            parts=path.split('.');parent=doc
            for part in parts[:-1]:
                if isinstance(parent,list):
                    if not part.isdecimal() or int(part)>=len(parent):raise ValueError('Invalid array update path')
                    parent=parent[int(part)]
                else:parent=parent.setdefault(part,{})
            key=parts[-1]
            if isinstance(parent,list):
                if not key.isdecimal() or int(key)>=len(parent):raise ValueError('Invalid array update path')
                key=int(key)
                if op in ('$set','$setOnInsert'):parent[key]=value
                elif op=='$unset':parent[key]=None
                elif op=='$inc':parent[key]+=value
                elif op=='$push':parent[key].append(value)
                continue
            if op in ('$set','$setOnInsert'):parent[key]=value
            elif op=='$unset':parent.pop(key,None)
            elif op=='$inc':parent[key]=parent.get(key,0)+value
            elif op=='$push':parent.setdefault(key,[]).append(value)
    return doc


class Cursor:
    def __init__(self,collection,filter,projection,session):
        self.collection=collection;self.filter=filter;self.projection=projection;self.session=session;self.order=None;self.maximum=None;self.offset=0
    def sort(self,key,direction=None): self.order=[(key,direction)] if isinstance(key,str) else key;return self
    def limit(self,n): self.maximum=n or None;return self
    def skip(self,n): self.offset=n;return self
    def __iter__(self):
        from .pg import operation_connection
        with operation_connection(self.session,self.collection.conn,read_only=True) as conn:
            collection=Collection(conn,self.collection.name)
            rows=collection.select(self.filter,self.order,self.maximum,self.offset,fields=self.projection)
        return iter([projection(row,self.projection) for row in rows])
