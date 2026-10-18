from pathlib import Path
import os,sys
from dotenv import load_dotenv
root=Path(__file__).resolve().parents[1]
if len(sys.argv)>1:load_dotenv(sys.argv[1],override=True)
sys.path[:0]=[str(root),str(root/'aislemint-ops/backend')]
import uvicorn
uvicorn.run('hackathon.server:app',host='127.0.0.1',port=int(os.environ.get('DEMO_PORT','8108')))
