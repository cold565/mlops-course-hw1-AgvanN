"""Общий запуск для интеграционных тестов: настоящие процессы, файлы и HTTP; модель — STUB.

Проверяемый проект задаётся HW8910_TARGET (по умолчанию — этот репозиторий). Интерфейс
одинаков для исходного прототипа и исправленной версии:
  <target>/lab.py --workspace W init|run [--report R]
  <target>/service.py --workspace W --port P --warmup-seconds S
Только стандартная библиотека.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = Path(os.environ.get('HW8910_TARGET', HERE.parent)).resolve()
PYTHON = os.environ.get('HW8910_PYTHON', sys.executable)
INJECT = HERE / 'inject.py'


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_events(ws: Path, model_id: str | None = None) -> list:
    path = ws / 'publications.jsonl'
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return [r for r in rows if model_id is None or r.get('model_id') == model_id]


class Result:
    def __init__(self, proc: subprocess.CompletedProcess):
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr

    def __repr__(self):
        return f'exit={self.returncode}\nSTDOUT:\n{self.stdout[-2000:]}\nSTDERR:\n{self.stderr[-2000:]}'


def lab(ws: Path, *args, fault: tuple | None = None, timeout: float = 180) -> Result:
    """lab.py в отдельном процессе с отдельным рабочим каталогом (cwd = каталог опыта)."""
    script = [str(TARGET / 'lab.py'), '--workspace', str(ws), *args]
    cmd = [PYTHON, str(INJECT), *fault, '--', *script] if fault else [PYTHON, *script]
    env = {k: v for k, v in os.environ.items() if not k.startswith('PREFECT_')}
    proc = subprocess.run(cmd, cwd=ws.parent, env=env, capture_output=True, text=True, timeout=timeout)
    return Result(proc)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def http(method: str, url: str, body=None, raw: bytes | None = None, timeout: float = 30):
    """-> (status, json|text). Тело JSON отправляется с Content-Type: application/json."""
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={'Content-Type': 'application/json'} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    try:
        return status, json.loads(payload)
    except ValueError:
        return status, payload.decode(errors='replace')


def answer_text(body: dict) -> str:
    return body.get('text', body.get('answer', ''))


class Service:
    """service.py как отдельный процесс на свободном порту localhost."""

    def __init__(self, ws: Path, warmup: float = 0.0, log_dir: Path | None = None):
        self.ws, self.warmup = ws, warmup
        self.port = free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.log_path = (log_dir or ws.parent) / f'service-{self.port}.log'
        self.proc = None

    def start(self, timeout: float = 60) -> 'Service':
        self.log = open(self.log_path, 'w')
        self.proc = subprocess.Popen([PYTHON, str(TARGET / 'service.py'), '--workspace', str(self.ws),
                                      '--port', str(self.port), '--warmup-seconds', str(self.warmup)],
                                     cwd=self.ws.parent, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f'service.py завершился: {self.log_path.read_text()}')
            try:
                if http('GET', self.url + '/health', timeout=2)[0] == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.1)
        raise TimeoutError('service.py не ответил на /health')

    def wait_ready(self, timeout: float = 60) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, body = http('GET', self.url + '/ready')
            if status == 200:
                return body
            time.sleep(0.1)
        raise TimeoutError('/ready не стал 200')

    def get(self, path):
        return http('GET', self.url + path)

    def post(self, path, body=None, raw=None):
        return http('POST', self.url + path, body=body, raw=raw)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc:
            self.log.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


class IntegrationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hw8910-it-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def new_workspace(self, name: str = 'ws') -> Path:
        """Новый workspace на каждый независимый опыт: прошлый опыт не скрывает ошибку."""
        ws = self.base / name
        result = lab(ws, 'init')
        self.assertEqual(result.returncode, 0, result)
        return ws
