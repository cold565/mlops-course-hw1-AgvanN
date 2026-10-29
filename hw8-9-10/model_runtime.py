"""Один код генерации для оценки и для API: тот же шаблон, system, decoding, dtype и устройство.

Повторяет генерацию ДЗ 6+7 (evaluation/cli.py:generate): chat template с system из протокола,
enable_thinking=False, greedy, max_new_tokens из протокола, decode без спецтокенов.
"""
import threading
from pathlib import Path

import artifacts
import release

BASE_FILES = ['config.json', 'generation_config.json', 'model.safetensors',
              'tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt']
TOKENIZER_FILES = ['tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt']
ADAPTER_FILES = ['adapter_config.json', 'adapter_model.safetensors']


def resolve_device(name: str) -> str:
    import torch
    if name != 'auto':
        return name
    if torch.cuda.is_available():
        return 'cuda'
    if torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'


def _dtype(name: str):
    import torch
    return {'float32': torch.float32, 'bfloat16': torch.bfloat16, 'float16': torch.float16}[name]


def library_versions() -> dict:
    import peft
    import torch
    import transformers
    return {'torch': torch.__version__, 'transformers': transformers.__version__, 'peft': peft.__version__}


def render_prompt(tokenizer, system: str, prompt: str, enable_thinking: bool) -> str:
    return tokenizer.apply_chat_template(
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}],
        tokenize=False, add_generation_prompt=True, enable_thinking=enable_thinking)


def load_tokenizer(base_root: Path):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(base_root), local_files_only=True)


def build_runtime(base_root: Path, base_name: str, revision: str, *, system: str, max_new_tokens: int,
                  enable_thinking: bool, dtype: str, device: str, adapter: bool) -> dict:
    """Фактические условия генерации. Любое изменение -> другой runtime_sha256 -> новая оценка."""
    if len(revision) != 40:
        raise ValueError('Нужна точная commit-ревизия базы')
    tokenizer = load_tokenizer(base_root)
    return {
        'base_model': base_name, 'base_revision': revision, 'adapter': adapter,
        'tokenizer_sha256': release.files_digest(release.hash_files(base_root, TOKENIZER_FILES)),
        'template_sha256': release.sha256_bytes(tokenizer.chat_template.encode('utf-8')),
        'generation': {'system': system, 'system_sha256': release.sha256_bytes(system.encode('utf-8')),
                       'do_sample': False, 'max_new_tokens': max_new_tokens,
                       'enable_thinking': enable_thinking},
        'dtype': dtype, 'device': device, 'libraries': library_versions(),
    }


def base_root_for(runtime: dict) -> Path:
    return artifacts.artifact_root({'artifact_uri': f"hf://{runtime['base_model']}@{runtime['base_revision']}"}, Path('.'))


class Generator:
    def __init__(self, base_root: Path, runtime: dict, adapter_root: Path | None = None):
        import torch
        from transformers import AutoModelForCausalLM
        self.torch = torch
        self.runtime = runtime
        gen = runtime['generation']
        self.system, self.enable_thinking = gen['system'], gen['enable_thinking']
        self.max_new_tokens = gen['max_new_tokens']
        self.device = torch.device(runtime['device'])
        self.tokenizer = load_tokenizer(base_root)
        self._tok_lock = threading.Lock()   # быстрый токенизатор не любит параллельный доступ
        self._gen_lock = threading.Lock()   # одна генерация на модель за раз
        model = AutoModelForCausalLM.from_pretrained(str(base_root), dtype=_dtype(runtime['dtype']),
                                                     local_files_only=True)
        if adapter_root is not None:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, str(adapter_root))
        self.model = model.to(self.device).eval()
        self.pad_id = self.tokenizer.pad_token_id or self.tokenizer.eos_token_id

    @classmethod
    def from_manifest(cls, manifest: dict, workspace: Path, verify: bool = True) -> 'Generator':
        """Загрузка по принятому manifest: пересчёт хешей файлов и сверка runtime с оценкой."""
        runtime = manifest['runtime']
        if release.sha256_json(runtime) != manifest['runtime_sha256']:
            raise ValueError('runtime_sha256 не соответствует runtime')
        base_root = base_root_for(runtime)
        adapter_root = None
        if runtime['adapter']:
            adapter_root = artifacts.artifact_root(manifest, workspace)
            if verify:
                release.verify_artifact_files(manifest, adapter_root)
                base = manifest['base_file_hashes']
                if release.hash_files(base_root, base) != base:
                    raise ValueError('Файлы базы не совпадают с manifest')
        elif verify:
            release.verify_artifact_files(manifest, base_root)
        actual = build_runtime(base_root, runtime['base_model'], runtime['base_revision'],
                               system=runtime['generation']['system'],
                               max_new_tokens=runtime['generation']['max_new_tokens'],
                               enable_thinking=runtime['generation']['enable_thinking'],
                               dtype=runtime['dtype'], device=runtime['device'], adapter=runtime['adapter'])
        if actual != runtime:
            diff = sorted(k for k in set(actual) | set(runtime) if actual.get(k) != runtime.get(k))
            raise ValueError(f'Runtime этого процесса не совпадает с runtime оценки: {diff}')
        return cls(base_root, runtime, adapter_root)

    def encode(self, prompt: str) -> list:
        with self._tok_lock:
            text = render_prompt(self.tokenizer, self.system, prompt, self.enable_thinking)
            return self.tokenizer(text)['input_ids']

    def count_prompt_tokens(self, prompt: str) -> int:
        return len(self.encode(prompt))

    def generate_ids(self, input_ids: list, max_new_tokens: int) -> dict:
        torch = self.torch
        batch = torch.tensor([input_ids], device=self.device)
        with self._gen_lock, torch.inference_mode():
            out = self.model.generate(input_ids=batch, attention_mask=torch.ones_like(batch),
                                      max_new_tokens=max_new_tokens, do_sample=False, pad_token_id=self.pad_id)
        new_ids = out[0, batch.shape[1]:]
        with self._tok_lock:
            text = self.tokenizer.decode(new_ids, skip_special_tokens=True)
        return {'text': text, 'output_tokens': int(new_ids.shape[0])}

    def generate(self, prompt: str, max_new_tokens: int) -> dict:
        return self.generate_ids(self.encode(prompt), max_new_tokens)
