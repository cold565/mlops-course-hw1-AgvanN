"""Общий интерфейс backend для API и тестов: StubBackend (без модели) и TransformersBackend.

Backend загружает ровно то, что записано в принятом manifest (указателе) на момент старта,
и сам сообщает, какая версия у него в памяти. Указатель после старта не перечитывается.
"""
import threading
import time
from pathlib import Path

import artifacts
import release


class Backend:
    kind = 'abstract'

    def __init__(self, pointer: dict, workspace: Path):
        self.pointer = pointer
        self.workspace = Path(workspace)
        self.model_id = pointer['model_id']
        self.runtime_sha256 = pointer['runtime_sha256']
        self.release_id = (pointer.get('release') or {}).get('release_id')
        self.max_new_tokens = int(pointer['runtime']['generation']['max_new_tokens'])
        self.calls = 0
        self._lock = threading.Lock()

    def _count_call(self):
        with self._lock:
            self.calls += 1

    def load(self) -> None:
        raise NotImplementedError

    def warmup(self) -> None:
        raise NotImplementedError

    def count_tokens(self, prompt: str) -> int:
        raise NotImplementedError

    def generate(self, prompt: str, max_new_tokens: int) -> dict:
        """-> {'text': str, 'output_tokens': int}"""
        raise NotImplementedError


class StubBackend(Backend):
    """STUB ONLY: ответ строится из реально загруженного файла артефакта, не из указателя."""
    kind = 'stub'

    def __init__(self, pointer, workspace, delay: float = 0.01):
        super().__init__(pointer, workspace)
        self.delay = delay
        self.artifact_text = None

    def load(self):
        root = artifacts.artifact_root(self.pointer, self.workspace)
        release.verify_artifact_files(self.pointer, root)
        files = sorted(self.pointer['file_hashes'])
        self.artifact_text = ' '.join((root / f).read_text(encoding='utf-8').strip() for f in files)

    def warmup(self):
        pass  # прогрев не считается вызовом обслуживания

    def count_tokens(self, prompt):
        return len(prompt.split())

    def generate(self, prompt, max_new_tokens):
        self._count_call()
        time.sleep(self.delay)
        words = f'STUB ONLY: {self.model_id} [{self.artifact_text}]'.split()
        return {'text': ' '.join(words[:max_new_tokens]), 'output_tokens': min(len(words), max_new_tokens)}


class TransformersBackend(Backend):
    """База (+ LoRA через PEFT) с тем же runtime, что использовался при оценке."""
    kind = 'transformers'

    def __init__(self, pointer, workspace):
        super().__init__(pointer, workspace)
        self.generator = None

    def load(self):
        import model_runtime
        self.generator = model_runtime.Generator.from_manifest(self.pointer, self.workspace, verify=True)

    def warmup(self):
        self.generator.generate('Warm-up: 2+2?', 4)

    def count_tokens(self, prompt):
        return self.generator.count_prompt_tokens(prompt)

    def generate(self, prompt, max_new_tokens):
        self._count_call()
        return self.generator.generate(prompt, max_new_tokens)


def make_backend(kind: str, **options):
    def factory(pointer, workspace):
        if kind == 'stub':
            return StubBackend(pointer, workspace, **options)
        if kind == 'transformers':
            return TransformersBackend(pointer, workspace)
        raise ValueError(f'Неизвестный backend {kind}')
    return factory
