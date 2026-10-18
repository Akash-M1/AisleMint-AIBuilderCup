"""Observed inventory bridge from shared data or closed OneSales ledgers."""
from fastapi import APIRouter,HTTPException
from ..pg import database,MongoRoute
from . import auth,modelling
from .repository import required_shop
router=APIRouter(prefix='/api/retail',route_class=MongoRoute,tags=['Inventory position'])
@router.get('/shops/{workspace}/inventory-position')
def inventory_position(workspace:str):
    auth.require_workspace(workspace);s=required_shop(workspace)
    product=database().data_products.find_one({'_id':workspace})
    if product and product.get('config',{}).get('inventory') and product.get('active_release'):
        from ..data_platform.service import stock_position
        run=modelling.resolve(workspace,'demand');e=modelling.demand_engine(workspace,run['id'])
        return stock_position(workspace,str(e.last.date()),set(e.panel.product_id.astype(str)))
    if not s.get('pos_store_id'):raise HTTPException(409,'Connect this shop to OneSales before loading observed stock.')
    run=modelling.resolve(workspace,'demand');e=modelling.demand_engine(workspace,run['id']);as_of=str(e.last.date());db=database()
    if not db.pos_days.find_one({'store_id':s['pos_store_id'],'business_date':as_of}):raise HTTPException(409,'OneSales has no closed-day stock record at this model’s forecast origin. Supply matching observed stock or train on current history.')
    records=list(db.pos_inventory_movements.aggregate([{'$match':{'store_id':s['pos_store_id'],'business_date':{'$lte':as_of}}},
        {'$sort':{'business_date':-1,'created':-1,'_sequence':-1}},
        {'$group':{'_id':'$product_id','on_hand':{'$first':'$balance'}}}]))
    balances={r['_id']:r['on_hand'] for r in records};positions=[]
    for product,g in e.panel.groupby('product_id'):
        last=g.sort_values('date').iloc[-1]
        positions.append(dict(product_id=str(product),store_id=s['store_code'],on_hand=balances.get(str(product)),unit_cost=float(last.unit_cost) if 'unit_cost' in g else None))
    return dict(as_of=as_of,positions=positions,source='OneSales closed-day inventory ledger')

