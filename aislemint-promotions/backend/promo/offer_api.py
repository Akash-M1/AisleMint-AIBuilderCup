from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
"""Inspectable conditional-offer economics and local model provenance."""
from fastapi import APIRouter
from pydantic import BaseModel,ConfigDict,Field
from .offer_mechanics import ConditionalOffer,checkout,offer_profile,redemption_model

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/offer-mechanics',tags=['Offer mechanics'])


class BasketRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    product_id:str
    cart:dict[str,int]
    regular_prices:dict[str,float]
    discount:float=Field(default=0,ge=0,le=.5)
    mechanics:list[ConditionalOffer]=Field(default_factory=list,max_length=6)
    selected_rewards:dict[str,str]=Field(default_factory=dict)


@router.post('/checkout')
def basket(req:BasketRequest):
    if len(req.cart)>30 or len(req.regular_prices)>30:raise ValueError('Limit basket previews to 30 products.')
    return checkout(req.cart,req.regular_prices,req.product_id,req.discount,req.mechanics,req.selected_rewards)


@router.get('/model')
def model_card():return redemption_model()['card']
