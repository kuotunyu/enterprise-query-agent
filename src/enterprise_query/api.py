from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .contracts import QuestionRequest
from .service import Service

def create_app(service=None, check_startup=True):
    service=service or Service()
    @asynccontextmanager
    async def lifespan(app):
        if os.getenv('EQA_BOOTSTRAP_PASSWORD'):
            raise RuntimeError('Bootstrap credentials cannot be present in the web process')
        if check_startup:
            app.state.identity=await asyncio.to_thread(service.executor_factory().check_identity)
        yield
    app=FastAPI(title='Enterprise Query Agent',lifespan=lifespan)
    static=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')
    @app.get('/')
    def index(): return FileResponse(static/'index.html')
    @app.get('/api/meta')
    def meta(): return {'mode':service.provider.mode,'identity':getattr(app.state,'identity',{}),'coverage':'來源分析 policy：2017-01 至 2018-08；合成資料另標示，非完整性保證。'}
    @app.post('/api/ask')
    async def ask(request:QuestionRequest): return await service.ask(request)
    @app.post('/api/cancel/{session_id}')
    async def cancel(session_id:str): return await asyncio.to_thread(service.cancel,session_id)
    return app

app=create_app()
