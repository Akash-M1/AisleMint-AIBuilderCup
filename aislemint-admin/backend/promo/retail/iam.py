"""Tenant-owner IAM. All grants are scoped, versioned, transactional and audited."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from ..pg import database, connection, MongoRoute
from . import auth, permissions
from .repository import audit, now, uid

router = APIRouter(prefix='/api/retail', route_class=MongoRoute, tags=['Identity & access'])
Level = Literal['none', 'viewer', 'user', 'editor']


class AccessInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    membership: Literal['owner', 'member'] | None = 'member'
    roles: dict[str, Level] = Field(default_factory=dict)


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(min_length=12, max_length=128)
    membership: Literal['owner', 'member'] = 'member'
    roles: dict[str, Level] = Field(default_factory=dict)


def validate_roles(roles):
    if set(roles) - set(permissions.AREAS):
        raise HTTPException(422, 'Unknown product area in role assignment.')
    return {area: roles.get(area, 'none') for area in permissions.AREAS}


def user_view(user, retailer_id):
    return dict(id=user['id'], name=user['name'], email=user['email'],
                membership=user.get('memberships', {}).get(retailer_id),
                roles=permissions.roles(user, retailer_id),
                revision=user.get('access_revision', 0), disabled=user.get('disabled', False))


@router.get('/permissions')
def permission_catalog():
    auth.account()
    return permissions.schema()


@router.get('/retailers/{retailer_id}/accounts')
def accounts(retailer_id: str):
    auth.require_retailer(retailer_id, owner=True)
    return [user_view(u, retailer_id) for u in database().retail_users.find(
        {f'memberships.{retailer_id}': {'$exists': True}, 'admin': False}).sort('name', 1)]


@router.post('/retailers/{retailer_id}/accounts')
def create_account(retailer_id: str, req: AccountInput):
    auth.require_retailer(retailer_id, owner=True)
    db = database()
    if not db.retailers.find_one({'_id': retailer_id}):
        raise HTTPException(404, 'Retailer not found.')
    assigned = validate_roles(req.roles)
    email = auth.email_address(req.email)
    if db.retail_users.find_one({'email': email}):
        raise HTTPException(409, 'This email already has an account. A platform administrator must manage cross-retailer membership.')
    id = uid()
    row = dict(_id=id, id=id, name=req.name.strip(), email=email, admin=False,
               disabled=False, memberships={retailer_id: req.membership},
               module_roles={retailer_id: assigned}, access_revision=0,
               created=now(), **auth.credentials(req.password))
    with connection() as c:
        c.begin()
        # Serialize owner changes, including simultaneous removal of two owners.
        db.retailers.update_one({'_id': retailer_id}, {'$inc': {'iam_revision': 1}}, session=c.session)
        db.retail_users.insert_one(row, session=c.session)
        audit('account_created', retailer_id, auth.actor(),
              {'user_id': id, 'membership': req.membership, 'roles': assigned}, session=c.session)
    return user_view(row, retailer_id)


def update_access(retailer_id, user_id, req):
    auth.require_retailer(retailer_id, owner=True)
    db = database()
    assigned = validate_roles(req.roles)
    with connection() as c:
        c.begin()
        retailer = db.retailers.update_one({'_id': retailer_id}, {'$inc': {'iam_revision': 1}}, session=c.session)
        if not retailer.matched_count:
            raise HTTPException(404, 'Retailer not found.')
        user = db.retail_users.find_one({'_id': user_id, 'admin': False}, session=c.session)
        if not user or (retailer_id not in user.get('memberships', {}) and not auth.account().get('admin')):
            raise HTTPException(404, 'Retailer account not found.')
        if user.get('access_revision', 0) != req.revision:
            raise HTTPException(409, 'Access changed since you opened this account. Refresh and review the current roles.')
        before = user.get('memberships', {}).get(retailer_id)
        if before == 'owner' and req.membership != 'owner':
            owners = db.retail_users.count_documents({f'memberships.{retailer_id}': 'owner', 'disabled': False}, session=c.session)
            if owners <= 1:
                raise HTTPException(409, 'Assign another active retailer owner before removing the last owner.')
        update = {'$inc': {'access_revision': 1}}
        if req.membership:
            update['$set'] = {f'memberships.{retailer_id}': req.membership, f'module_roles.{retailer_id}': assigned}
        else:
            update['$unset'] = {f'memberships.{retailer_id}': '', f'module_roles.{retailer_id}': ''}
        db.retail_users.update_one({'_id': user_id}, update, session=c.session)
        # Accounts are reloaded on every request; revocation also invalidates all sessions.
        db.retail_sessions.delete_many({'user_id': user_id}, session=c.session)
        audit('account_access_changed', retailer_id, auth.actor(),
              {'user_id': user_id, 'before': user_view(user, retailer_id),
               'membership': req.membership, 'roles': assigned, 'revision': req.revision + 1}, session=c.session)
    return {'status': 'updated', 'revision': req.revision + 1}


@router.post('/retailers/{retailer_id}/accounts/{user_id}/access')
def change_access(retailer_id: str, user_id: str, req: AccessInput):
    return update_access(retailer_id, user_id, req)


@router.get('/retailers/{retailer_id}/access-audit')
def access_audit(retailer_id: str):
    auth.require_retailer(retailer_id, owner=True)
    return [{k: v for k, v in row.items() if k != '_id'} for row in database().retail_audit.find(
        {'workspace': retailer_id, 'event': {'$in': ['account_created', 'account_access_changed', 'retailer_account_created', 'retailer_access_changed']}}
    ).sort('created', -1).limit(100)]
