"""Product permissions shared by IAM, HTTP enforcement and the platform shell.

Existing owner/planner/viewer memberships remain valid. Explicit module roles are
authoritative for members; an omitted area grants no access. Never infer rights
from a UI route, a staff PIN, or a role submitted with an operation.
"""
from fastapi import HTTPException
from contextvars import ContextVar

entitlement_cache = ContextVar('paid_entitlements', default=None)

AREAS = ('promotions', 'demand', 'sales', 'data', 'connect')
LEVELS = {'none': 0, 'viewer': 1, 'user': 2, 'editor': 3}


def roles(principal, retailer_id):
    membership = principal.get('memberships', {}).get(retailer_id)
    if principal.get('admin'):
        return dict.fromkeys(AREAS, 'editor')
    if not membership:
        return dict.fromkeys(AREAS, 'none')
    explicit = principal.get('module_roles', {}).get(retailer_id)
    if membership == 'owner':
        result = dict.fromkeys(AREAS, 'editor')
    elif explicit is not None:
        result = {area: explicit.get(area, 'none') for area in AREAS}
    else:
        result = dict.fromkeys(AREAS, {'planner': 'editor', 'viewer': 'viewer'}.get(membership, 'none'))
    # Entitlements belong to the tenant, not a user-editable membership. Owners,
    # staff and integration identities share this ceiling. Legacy tenants retain
    # their existing access until explicitly migrated to a paid subscription.
    from ..pg import database
    from .repository import now
    cache = entitlement_cache.get()
    if cache is not None and retailer_id in cache:
        retailer = cache[retailer_id]
    else:
        retailer = database().retailers.find_one({'_id': retailer_id})
        if cache is not None:cache[retailer_id] = retailer
    subscription = (retailer or {}).get('subscription')
    if subscription is not None:
        allowed = subscription.get('areas', []) if subscription.get('expires_at', now()) > now() else []
        result = {area: value if area in allowed else 'none' for area, value in result.items()}
    return result


def allows(principal, retailer_id, area, level='viewer'):
    return LEVELS.get(roles(principal, retailer_id).get(area), 0) >= LEVELS[level]


def require(retailer_id, area, level='viewer'):
    from .auth import account, require_retailer
    require_retailer(retailer_id)
    if not allows(account(), retailer_id, area, level):
        raise HTTPException(403, f'{area.title()} {level.title()} access is required. Ask your retailer owner to update your roles.')


def workspace(workspace_id, area, level='viewer'):
    from .auth import account, require_workspace
    row = require_workspace(workspace_id)
    if row:
        require(row['retailer_id'], area, level)
    elif not account().get('admin'):
        raise HTTPException(403, 'Workspace access is required.')


def schema():
    return {'version': 1, 'areas': list(AREAS), 'levels': [
        {'id': 'none', 'name': 'No access', 'description': 'This product area is unavailable.'},
        {'id': 'viewer', 'name': 'Viewer', 'description': 'View existing results, plans and records.'},
        {'id': 'user', 'name': 'User', 'description': 'Run forecasts and analysis; operate checkout and returns with an authorized staff PIN.'},
        {'id': 'editor', 'name': 'Editor', 'description': 'Also save and confirm plans, train models and manage operational configuration. Owner approval remains required for protected actions.'},
    ], 'owner': 'Manages retailer accounts and all shops, approves model activation and sensitive connections.'}


def any_workspace(workspace_id, areas, level='viewer'):
    from .auth import account, require_workspace
    row = require_workspace(workspace_id)
    if row and not any(allows(account(), row['retailer_id'], area, level) for area in areas):
        raise HTTPException(403, 'Your account does not have access to this operation.')


def check_request(request, principal):
    """Second boundary after authentication/tenant checks, before endpoint dispatch.

    Unknown shop operations fail closed. Shared connector operations can serve
    Data or Connect, but their existing source visibility/owner checks still run.
    Demand POST bodies are validated by the gateway before their domain handler.
    """
    import re
    from .auth import admin
    from ..pg import database
    if principal.get('admin'):
        return
    if principal.get('must_change_password'):
        return  # check_scope restricts this principal to password/me/logout.
    if principal.get('integration'):
        for retailer_id in principal.get('memberships', {}):
            require(retailer_id, 'data', 'editor')
        return
    path = request.url.path
    if path in ('/api/demo/reset', '/api/demo/pos-session'):
        return  # These handlers verify the synthetic guest and its own workspace.
    write = request.method not in ('GET', 'HEAD', 'OPTIONS')
    level = 'editor' if write else 'viewer'
    match = re.fullmatch(r'/api/retail/shops/([^/]+)/(.*)', path)
    if match:
        shop_id, operation = match.groups()
        if operation == 'inventory-position':
            return any_workspace(shop_id, ('promotions', 'demand', 'sales'))
        if operation == 'pos':
            return workspace(shop_id, 'sales', 'editor')
        if operation.startswith('connected/'):
            shared = operation.startswith(('connected/sources', 'connected/runs', 'connected/quarantine')) or operation == 'connected/overview'
            if operation == 'connected/ask':
                level = 'viewer'  # Evidence search cannot execute operational actions.
            elif operation == 'connected/decisions' or operation.endswith('/preview'):
                level = 'user' if write else 'viewer'
            return any_workspace(shop_id, ('connect', 'data') if shared else ('connect',), level)
        if operation == 'data/analytics':
            return workspace(shop_id, 'sales')
        if operation.startswith('data/') or operation in ('workbench', 'train', 'ledger-snapshot') or operation.startswith(('snapshots', 'runs/')):
            return workspace(shop_id, 'data', level)
        raise HTTPException(403, 'No product permission is defined for this operation.')
    if path.startswith('/api/retail/'):
        # Account endpoints enforce tenant ownership themselves; no product role
        # grants membership administration, platform admin or integration keys.
        return
    if path.startswith('/api/pos/'):
        match = re.match(r'/api/pos/stores/([^/]+)(.*)', path)
        if match:
            row = database().pos_stores.find_one({'id': match[1]})
            suffix = match[2]
            if write:
                level = 'viewer' if suffix in ('/login', '/logout') else 'user'
                if suffix.startswith(('/staff', '/products', '/stock', '/planning-connection', '/recording', '/learning/settings', '/holidays')):
                    level = 'editor'
            retailer_id = row.get('retailer_id', '') if row else ''
            if write and suffix in ('/learning/train', '/learning/settings'):
                require(retailer_id, 'data', 'editor')
            return require(retailer_id, 'sales', level)
        if path == '/api/pos/stores' and not write:
            return  # The list is filtered by Sales access below the tenant boundary.
        return admin()
    if path.startswith('/api/demand/'):
        if path == '/api/demand/sources' and not write:
            return  # Filtered by Demand access in the handler.
        if write:
            return  # Source lives in the body; the gateway checks it explicitly.
        return workspace(request.query_params.get('source_id', 'benchmark'), 'demand')
    if path.startswith('/api/offer-mechanics/'):
        return any_workspace(request.headers.get('x-workspace-id', 'benchmark'), ('promotions', 'demand'), 'user' if write else 'viewer')
    if path in ('/api/summary', '/api/catalog', '/api/hindsight', '/api/forecast', '/api/optimize', '/api/scenarios', '/api/audit') or path.startswith(('/api/promotions', '/api/scenarios/')):
        if path in ('/api/forecast', '/api/optimize', '/api/promotions/flyer-scan', '/api/promotions/campaign-kit'):
            level = 'user'
        return workspace(request.headers.get('x-workspace-id', 'benchmark'), 'promotions', level)
    if path == '/api/datasets' and not write:
        return  # Legacy list returns no global datasets to retailer accounts.
    # Benchmark training, global datasets and platform diagnostics are operator-only.
    return admin()
