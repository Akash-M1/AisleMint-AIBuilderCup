"""Bound JSON request memory before parsing, including chunked HTTP requests."""
from starlette.responses import JSONResponse

class RequestLimit:
    def __init__(self,app,maximum=8*1024*1024):self.app=app;self.maximum=maximum
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method') in ('GET','HEAD','OPTIONS'):
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        try:declared=int(headers.get(b'content-length',b'0'))
        except ValueError:declared=self.maximum+1
        if declared>self.maximum:
            return await JSONResponse({'detail':'Request exceeds 8 MiB. Use smaller JSON batches.'},413)(scope,receive,send)
        messages=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            size+=len(message.get('body',b''))
            if size>self.maximum:return await JSONResponse({'detail':'Request exceeds 8 MiB. Use smaller JSON batches.'},413)(scope,receive,send)
            messages.append(message)
            if not message.get('more_body',False):break
        index=0
        async def buffered():
            nonlocal index
            if index<len(messages):result=messages[index];index+=1;return result
            return await receive()
        await self.app(scope,buffered,send)
