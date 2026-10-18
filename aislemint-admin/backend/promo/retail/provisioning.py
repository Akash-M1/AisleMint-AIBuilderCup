"""Narrow, signed billing-to-platform boundary. No browser can grant a plan."""
from datetime import timedelta
import hashlib
import hmac
import os
import time
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from ..pg import connection, database, retry_transaction
from . import auth
from .repository import now, uid

VERSION = 'annual-2026-10'
PLANS = {'basic': 1500000, 'premium': 2500000, 'ultra': 5000000}
AREAS = {'basic': ['promotions', 'demand', 'data'],
         'premium': ['promotions', 'demand', 'data', 'sales'],
         'ultra': ['promotions', 'demand', 'data', 'sales', 'connect']}


class Purchase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    order_id: str = Field(pattern=r'^AM-[a-f0-9-]{36}$')
    payment_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    pricing_version: str
    plan_id: str
    amount_paise: int
    currency: str
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(max_length=254)
    business: str = Field(min_length=2, max_length=120)
    temporary_password: str = Field(min_length=24, max_length=128, repr=False)


def verify(request, raw):
    secret = os.environ.get('BILLING_PROVISIONING_SECRET', '')
    if len(secret) < 32:
        raise HTTPException(503, 'Account provisioning is not configured.')
    stamp = request.headers.get('x-aislemint-timestamp', '')
    signature = request.headers.get('x-aislemint-signature', '')
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > 300:
        raise HTTPException(401, 'Invalid provisioning authorization.')
    expected = hmac.new(secret.encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, 'Invalid provisioning authorization.')


@retry_transaction
def provision(purchase):
    if (purchase.pricing_version != VERSION or purchase.currency != 'INR'
            or PLANS.get(purchase.plan_id) != purchase.amount_paise):
        raise HTTPException(409, 'Purchase does not match the annual catalog.')
    email = auth.email_address(purchase.email)
    fingerprint = hashlib.sha256(purchase.model_dump_json().encode()).hexdigest()
    db = database()
    with connection() as c:
        # Serialize purchases for an email; unique email and audit primary keys
        # plus serializable retries protect simultaneous deliveries and retries.
        c.conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('billing:' + email,))
        existing = db.retail_audit.find_one({'_id': 'purchase:' + purchase.order_id})
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise HTTPException(409, 'Order was already provisioned with different details.')
            return existing['result']
        user = db.retail_users.find_one({'email': email})
        if user and (user.get('disabled') or user.get('admin') or user.get('must_change_password')):
            raise HTTPException(409, 'This account needs administrator review before another workspace is added.')
        started = now()
        try:
            expires = started.replace(year=started.year + 1)
        except ValueError:  # February 29 -> February 28 of the following year.
            expires = started.replace(year=started.year + 1, day=28)
        retailer_id, shop_id = uid(), uid()
        db.retailers.insert_one(dict(_id=retailer_id, id=retailer_id, name=purchase.business,
            created=started, subscription=dict(plan_id=purchase.plan_id, areas=AREAS[purchase.plan_id],
                order_id=purchase.order_id, starts_at=started, expires_at=expires,
                pricing_version=VERSION, renewal='manual')))
        db.retail_shops.insert_one(dict(_id=shop_id, id=shop_id, retailer_id=retailer_id,
            name=purchase.business, store_code='MAIN', country='IN', timezone='Asia/Kolkata',
            currency='INR', created=started, revision=0, active={}))
        is_new = user is None
        if is_new:
            user_id = uid()
            db.retail_users.insert_one(dict(_id=user_id, id=user_id, email=email,
                name=purchase.name, admin=False, disabled=False, memberships={retailer_id: 'owner'},
                created=started, must_change_password=True,
                temporary_password_expires_at=started + timedelta(hours=72),
                **auth.credentials(purchase.temporary_password)))
        else:
            user_id = user['id']
            # Never overwrite an existing customer's password or other access.
            db.retail_users.update_one({'_id': user_id}, {'$set': {f'memberships.{retailer_id}': 'owner'}})
        result = dict(order_id=purchase.order_id, retailer_id=retailer_id, shop_id=shop_id,
            new_account=is_new, starts_at=started.isoformat(), expires_at=expires.isoformat(),
            temporary_password_expires_at=(started + timedelta(hours=72)).isoformat() if is_new else None)
        db.retail_audit.insert_one(dict(_id='purchase:' + purchase.order_id, id=uid(),
            event='paid_workspace_provisioned', workspace=retailer_id, actor='billing-service',
            created=started, fingerprint=fingerprint, result=result,
            details={'order_id': purchase.order_id, 'payment_id': purchase.payment_id,
                     'plan_id': purchase.plan_id, 'amount_paise': purchase.amount_paise}))
        return result
