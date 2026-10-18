"""Email-possession recovery through the private, signed billing mail service."""
from datetime import timedelta
import hmac
import re
import secrets
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from ..pg import connection, database, retry_transaction
from . import auth
from .repository import now


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(max_length=254)
    requester_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class ResetComplete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[a-f0-9]{32}\.[A-Za-z0-9_-]{43}$', repr=False)
    password: str = Field(min_length=12, max_length=128, repr=False)


def limited(db, key, maximum, time):
    row = db.retail_login_attempts.find_one_and_update({'_id': key},
        {'$inc': {'attempts': 1}, '$setOnInsert': {'expires_at': time + timedelta(hours=1)}},
        upsert=True, return_document=True)
    if row['expires_at'] <= time:
        db.retail_login_attempts.update_one({'_id': key},
            {'$set': {'attempts': 1, 'expires_at': time + timedelta(hours=1)}})
        return False
    return row['attempts'] > maximum


@retry_transaction
def prepare(req):
    email = auth.email_address(req.email)
    db = database()
    time = now()
    with connection() as c:
        c.conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('recovery:' + email,))
        ip_limit = limited(db, 'recovery:ip:' + req.requester_hash, 20, time)
        email_limit = limited(db, 'recovery:email:' + auth.digest(email), 3, time)
        if ip_limit or email_limit:
            return {'recovery': None}
        user = db.retail_users.find_one({'email': email, 'disabled': False, 'admin': False})
        if not user:
            return {'recovery': None}
        previous = user.get('password_reset', {})
        if previous.get('issued_at') and previous['issued_at'] > time - timedelta(minutes=1):
            return {'recovery': None}
        token = user['id'] + '.' + secrets.token_urlsafe(32)
        expires = time + timedelta(minutes=30)
        db.retail_users.update_one({'_id': user['id']}, {'$set': {'password_reset': {
            'digest': auth.digest(token), 'expires_at': expires, 'issued_at': time,
            'password_hash_at_issue': user['password_hash']}}})
        # This result is returned only over the signed internal boundary. The
        # public mail service never returns it to the requesting browser.
        return {'recovery': {'token': token, 'email': email, 'expires_at': expires.isoformat()}}


@retry_transaction
def complete(req):
    if not re.fullmatch(r'[a-f0-9]{32}\.[A-Za-z0-9_-]{43}', req.token):
        raise HTTPException(400, 'This link is invalid or expired. Request a new one.')
    user_id = req.token.split('.')[0]
    db = database()
    with connection() as c:
        c.conn.execute('SELECT _id FROM retail_users WHERE _id=%s FOR UPDATE', (user_id,))
        user = db.retail_users.find_one({'_id': user_id, 'disabled': False, 'admin': False})
        reset = user.get('password_reset', {}) if user else {}
        if (not reset or reset['expires_at'] <= now()
                or not hmac.compare_digest(reset['digest'], auth.digest(req.token))
                or not hmac.compare_digest(reset['password_hash_at_issue'], user['password_hash'])):
            raise HTTPException(400, 'This link is invalid or expired. Request a new one.')
        if hmac.compare_digest(auth.password_hash(req.password, user['salt']), user['password_hash']):
            raise HTTPException(422, 'Choose a different new password.')
        db.retail_users.update_one({'_id': user_id}, {
            '$set': {**auth.credentials(req.password), 'must_change_password': False},
            '$unset': {'password_reset': '', 'temporary_password_expires_at': ''}})
        db.retail_sessions.delete_many({'user_id': user_id})
        return {'status': 'password_changed'}
