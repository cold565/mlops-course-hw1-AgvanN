"""FastAPI: /health, /ready, /generate (+ /debug только на тестовом стенде).

health — процесс жив. ready — веса из принятого manifest загружены и прогреты.
Загрузка идёт в фоне после старта (lifespan), до готовности /ready и /generate отвечают 503.
Некорректный запрос получает 4xx до вызова backend. Вычисления ограничены семафором.
"""
import asyncio
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

import release


class GenerateRequest(BaseModel):
    # Лишние поля (temperature, do_sample, ...) запрещены: менять условия генерации нельзя.
    model_config = ConfigDict(extra='forbid')
    prompt: StrictStr = Field(min_length=1, max_length=4000)
    max_new_tokens: StrictInt | None = None


class ServiceState:
    def __init__(self, pointer_path: Path, backend_factory, warmup_seconds: float):
        self.pointer_path = Path(pointer_path)
        self.backend_factory = backend_factory
        self.warmup_seconds = warmup_seconds
        self.backend = None
        self.ready = threading.Event()
        self.warmup_finished = threading.Event()
        self.error = None

    def load(self):
        started = time.monotonic()
        try:
            # Снимок указателя на момент старта. Смена accepted.json требует перезапуска.
            raw = self.pointer_path.read_bytes()
            pointer = json.loads(raw)
            if release.sha256_json(pointer['runtime']) != pointer['runtime_sha256']:
                raise ValueError('runtime_sha256 указателя не соответствует runtime')
            backend = self.backend_factory(pointer, self.pointer_path.parent)
            backend.load()          # проверка хешей файлов + загрузка весов
            backend.warmup()
            remaining = self.warmup_seconds - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            self.backend = backend
            self.pointer_sha256 = release.sha256_bytes(raw)
            self.warmup_finished.set()
            self.ready.set()
        except Exception as exc:  # процесс жив, но не готов: причина видна в /health
            self.error = f'{type(exc).__name__}: {exc}'


def create_app(pointer_path: Path, backend_factory, *, warmup_seconds: float = 0.0, debug: bool = False,
               max_input_tokens: int = 512, max_concurrency: int = 1, max_queue: int = 8,
               max_body_bytes: int = 65536) -> FastAPI:
    state = ServiceState(pointer_path, backend_factory, warmup_seconds)
    counters = {'pending': 0}

    @asynccontextmanager
    async def lifespan(app):
        app.state.semaphore = asyncio.Semaphore(max_concurrency)
        threading.Thread(target=state.load, name='model-loader', daemon=True).start()
        yield

    app = FastAPI(title='HW8910 model API', lifespan=lifespan)
    app.state.service = state

    @app.middleware('http')
    async def limit_body(request: Request, call_next):
        size = request.headers.get('content-length')
        if size is not None and (not size.isdigit() or int(size) > max_body_bytes):
            return JSONResponse({'error': 'body too large'}, status_code=413)
        return await call_next(request)

    def not_ready():
        return JSONResponse({'ready': False, 'error': state.error}, status_code=503)

    @app.get('/health')
    def health():
        return {'alive': True, 'ready': state.ready.is_set(), 'load_error': state.error}

    @app.get('/ready')
    def ready():
        if not state.ready.is_set():
            return not_ready()
        b = state.backend
        return {'ready': True, 'model_id': b.model_id, 'release_id': b.release_id,
                'runtime_sha256': b.runtime_sha256, 'backend': b.kind}

    if debug:
        @app.get('/debug')
        def debug_info():
            b = state.backend
            return {'loaded_id': b and b.model_id, 'calls': b.calls if b else 0,
                    'warmup_finished': state.warmup_finished.is_set(), 'ready': state.ready.is_set()}

    @app.post('/generate')
    async def generate(body: GenerateRequest):
        received = time.perf_counter()
        if not state.ready.is_set():
            return not_ready()
        backend = state.backend
        limit = backend.max_new_tokens if body.max_new_tokens is None else body.max_new_tokens
        if not 1 <= limit <= backend.max_new_tokens:
            return JSONResponse({'error': f'max_new_tokens должен быть в [1, {backend.max_new_tokens}] '
                                          '(лимит протокола оценки)'}, status_code=422)
        if not body.prompt.strip():
            return JSONResponse({'error': 'пустой prompt'}, status_code=422)
        n_tokens = await run_in_threadpool(backend.count_tokens, body.prompt)
        if n_tokens > max_input_tokens:
            return JSONResponse({'error': f'prompt длиннее {max_input_tokens} токенов ({n_tokens})'},
                                status_code=422)
        if counters['pending'] >= max_queue:
            return JSONResponse({'error': 'очередь заполнена'}, status_code=503, headers={'Retry-After': '1'})
        counters['pending'] += 1
        try:
            async with app.state.semaphore:
                started = time.perf_counter()
                result = await run_in_threadpool(backend.generate, body.prompt, limit)
                finished = time.perf_counter()
        finally:
            counters['pending'] -= 1
        return {'text': result['text'], 'request_id': str(uuid.uuid4()),
                'model_id': backend.model_id, 'release_id': backend.release_id,
                'runtime_sha256': backend.runtime_sha256, 'backend': backend.kind,
                'input_tokens': n_tokens, 'output_tokens': result['output_tokens'], 'max_new_tokens': limit,
                'queue_seconds': started - received, 'generation_seconds': finished - started,
                'total_seconds': finished - received}

    return app
