"""Ядро выпуска: ключ кеша, проверка связки manifest+report, атомарная запись указателя.

Только стандартная библиотека: модуль импортируют быстрые тесты, Prefect-flow и сервис.
Контракты — CONTRACTS.txt.
"""
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
HASH_FIELDS = ('model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256')
METRIC_FIELDS = ('accuracy', 'format_rate')


def canonical(value) -> bytes:
    """Канонический JSON: порядок ключей не важен, NaN/Inf запрещены."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value) -> str:
    return sha256_bytes(canonical(value))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- 1. ключ кеша

def cache_key(data_path: Path, config: dict) -> str:
    """SHA-256 от {хеш байтов входа, весь config}. Путь и mtime в ключ не входят."""
    if not isinstance(config, dict):
        raise ValueError('config должен быть dict')
    return sha256_json({'data_sha256': sha256_file(Path(data_path)), 'config': config})


# ------------------------------------------------------- хеши файлов артефакта

def hash_files(root: Path, relpaths) -> dict:
    """Закреплённый список файлов: относительный путь -> SHA-256 содержимого."""
    # Лексическая проверка: в кеше HF файлы снимка — symlink на blobs/, это допустимо.
    root = Path(os.path.normpath(os.path.abspath(root)))
    result = {}
    for rel in sorted(relpaths):
        path = Path(os.path.normpath(root / rel))
        if Path(rel).is_absolute() or not path.is_relative_to(root):
            raise ValueError(f'Файл артефакта вне каталога: {rel}')
        if not path.is_file():
            raise ValueError(f'Нет файла артефакта: {rel}')
        result[rel] = sha256_file(path)
    return result


def files_digest(file_hashes: dict) -> str:
    return sha256_json(file_hashes)


def verify_artifact_files(manifest: dict, root: Path) -> dict:
    """Пересчитать хеши реальных файлов и сверить с manifest (перед выпуском и загрузкой)."""
    pinned = manifest.get('file_hashes')
    if not isinstance(pinned, dict) or not pinned:
        raise ValueError('manifest не содержит закреплённый список file_hashes')
    actual = hash_files(root, pinned)
    for rel, expected in pinned.items():
        if actual[rel] != expected:
            raise ValueError(f'Хеш файла {rel} не совпадает с manifest')
    if files_digest(actual) != manifest.get('model_sha256'):
        raise ValueError('model_sha256 не соответствует файлам артефакта')
    return actual


# ------------------------------------------------------ 2. проверка выпуска

def _require_str(obj: dict, key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{where}.{key}: нужна непустая строка')
    return value


def _require_sha(obj: dict, key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not SHA256_RE.match(value):
        raise ValueError(f'{where}.{key}: нужен SHA-256 (64 строчных hex)')
    return value


def _require_metric(metrics: dict, key: str) -> None:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'metrics.{key}: нужно число')
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'metrics.{key}: нужно конечное число в [0, 1]')


def validate_release(manifest: dict, report: dict) -> None:
    """Полный завершённый отчёт PASS именно для этой модели и этого runtime, иначе ValueError."""
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError('manifest и report должны быть объектами')
    version = manifest.get('schema_version')
    if type(version) is not int or version != 1:
        raise ValueError('manifest.schema_version должен быть int 1')
    _require_str(manifest, 'model_id', 'manifest')
    _require_str(manifest, 'artifact_uri', 'manifest')
    for key in HASH_FIELDS:
        _require_sha(manifest, key, 'manifest')
    runtime = manifest.get('runtime')
    if not isinstance(runtime, dict) or not runtime:
        raise ValueError('manifest.runtime: нужен непустой объект')
    try:
        actual_runtime = sha256_json(runtime)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'manifest.runtime не сериализуется канонически: {exc}') from exc
    if actual_runtime != manifest['runtime_sha256']:
        raise ValueError('runtime_sha256 не соответствует runtime: нужна новая оценка')

    if report.get('status') != 'complete':
        raise ValueError(f"Отчёт не завершён: status={report.get('status')!r}")
    if report.get('passed') is not True:
        raise ValueError(f"Кандидат не допущен: passed={report.get('passed')!r}")
    _require_str(report, 'evaluation_run_id', 'report')
    if report.get('model_id') != manifest['model_id']:
        raise ValueError('Отчёт относится к другой модели (model_id)')
    for key in HASH_FIELDS:
        _require_sha(report, key, 'report')
        if report[key] != manifest[key]:
            raise ValueError(f'Отчёт не связан с manifest: различается {key}')
    metrics = report.get('metrics')
    if not isinstance(metrics, dict):
        raise ValueError('report.metrics: нужен объект')
    for key in METRIC_FIELDS:
        _require_metric(metrics, key)


def release_id(manifest: dict, report: dict) -> str:
    """Идентичность выпуска: модель + runtime + конкретное решение допуска (не только model_id)."""
    return sha256_json({
        'model_id': manifest['model_id'], 'model_sha256': manifest['model_sha256'],
        'runtime_sha256': manifest['runtime_sha256'], 'protocol_sha256': manifest['protocol_sha256'],
        'golden_sha256': manifest['golden_sha256'],
        'evaluation_run_id': report['evaluation_run_id'], 'report_sha256': sha256_json(report),
    })


# ------------------------------------------------------- 3. атомарная запись

def dump_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def atomic_write_bytes(target: Path, data: bytes) -> None:
    """Временный файл рядом с целевым, fsync, os.replace. При ошибке старый файл не тронут."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f'{target.name}.{uuid.uuid4().hex}.tmp'
    try:
        with open(tmp, 'xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    try:  # закрепить запись каталога (rename) на диске
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write_json(target: Path, value) -> None:
    atomic_write_bytes(target, dump_json(value))


def publish(manifest: dict, report: dict, target: Path) -> None:
    """validate_release, затем атомарная замена указателя. Одинаковый выпуск -> те же байты."""
    validate_release(manifest, report)
    data = dump_json(manifest)
    target = Path(target)
    if target.is_file() and target.read_bytes() == data:
        return
    atomic_write_bytes(target, data)
