"""Dedicated entry point. Never use this with the production database."""
import os,json
if os.environ.get('DEMO_RUNTIME_SECRET'):
 for key,value in json.loads(os.environ['DEMO_RUNTIME_SECRET']).items():
  if key in ('POSTGRES_PASSWORD','GEMINI_API_KEY'):os.environ[key]=value
if os.environ.get('AISLEMINT_HACKATHON')!='1' or os.environ.get('POSTGRES_DATABASE')!='aislemint_hackathon':
 raise RuntimeError('This entry point requires the isolated aislemint_hackathon database.')
from promo.api import app
from promo.hackathon import router
app.include_router(router)
