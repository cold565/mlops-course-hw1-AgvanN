"""Журналы выпуска: попытки отдельно от фактических публикаций. Один писатель.

Хранение (решение для этой работы):
  accepted.json          — указатель: manifest + блок release (release_id, отчёт, хеш отчёта).
                           Пишется атомарно (release.publish). Это источник истины о текущей версии.
  publications.jsonl     — только события фактической смены версии (publish/rollback).
  attempts.jsonl         — каждая попытка процесса, включая отказы, сбои и повторы.
  releases/<id>/         — неизменяемые копии manifest и отчёта каждого выпуска (для отката).

Порядок: сначала указатель, потом событие. Сбой между ними оставляет указатель без события.
Повтор видит release_id в указателе и дописывает недостающее событие (recovered=true),
но не публикует второй раз. Повтор полностью согласованного выпуска ничего не пишет.
"""
import json
import os
import time
from pathlib import Path

import release

POINTER = 'accepted.json'
PUBLICATIONS = 'publications.jsonl'
ATTEMPTS = 'attempts.jsonl'
RELEASES = 'releases'


class InjectedFailure(RuntimeError):
    """Управляемый сбой для опытов восстановления."""


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def append_jsonl(path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: Path) -> list:
    """Читает журнал; оборванная последняя строка (сбой посреди записи) пропускается."""
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    records = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                continue
            raise
    return records


def read_pointer(root: Path):
    path = Path(root) / POINTER
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def pointer_sha256(root: Path):
    path = Path(root) / POINTER
    return release.sha256_file(path) if path.exists() else None


def current_release_id(root: Path):
    pointer = read_pointer(root)
    if not pointer:
        return None
    return (pointer.get('release') or {}).get('release_id')


def publication_events(root: Path) -> list:
    return read_jsonl(Path(root) / PUBLICATIONS)


def record_attempt(root: Path, record: dict) -> None:
    append_jsonl(Path(root) / ATTEMPTS, {'ts': _now(), **record})


def build_pointer(manifest: dict, report: dict, report_uri: str) -> tuple:
    rid = release.release_id(manifest, report)
    pointer = {k: v for k, v in manifest.items() if k != 'release'}
    pointer['release'] = {'release_id': rid, 'evaluation_run_id': report['evaluation_run_id'],
                          'report_uri': report_uri, 'report_sha256': release.sha256_json(report)}
    return rid, pointer


def publish_release(root: Path, manifest: dict, report: dict, report_uri: str, *,
                    event: str = 'publish', context: dict | None = None,
                    fail_after_pointer: bool = False) -> dict:
    """Идемпотентный выпуск. Возвращает {'outcome': published|already_published|recovered, ...}.

    ValueError (отказ/несовместимость) выходит наружу до любой записи.
    """
    root = Path(root)
    release.validate_release(manifest, report)
    rid, pointer = build_pointer(manifest, report, report_uri)
    target = root / POINTER
    current = read_pointer(root)
    current_rid = ((current or {}).get('release') or {}).get('release_id')
    events = publication_events(root)
    last_rid = events[-1]['release_id'] if events else None

    # Неизменяемая копия для отката: пишется до указателя, повтор даёт те же байты.
    store = root / RELEASES / rid
    release.atomic_write_json(store / 'manifest.json', {k: v for k, v in manifest.items() if k != 'release'})
    release.atomic_write_json(store / 'report.json', report)

    base = {'release_id': rid, 'model_id': manifest['model_id'], 'model_sha256': manifest['model_sha256'],
            'runtime_sha256': manifest['runtime_sha256'], 'evaluation_run_id': report['evaluation_run_id'],
            'report_sha256': release.sha256_json(report), **(context or {})}

    if current_rid == rid:
        if last_rid == rid:
            return {'outcome': 'already_published', 'release_id': rid}
        # Указатель уже обновлён, события нет: сбой между указателем и журналом.
        append_jsonl(root / PUBLICATIONS, {'ts': _now(), 'event': event, 'recovered': True,
                                           'previous_release_id': last_rid, **base})
        return {'outcome': 'recovered', 'release_id': rid}

    release.publish(pointer, report, target)
    if fail_after_pointer:
        raise InjectedFailure('Инъекция сбоя между записью указателя и журнала')
    append_jsonl(root / PUBLICATIONS, {'ts': _now(), 'event': event, 'recovered': False,
                                       'previous_release_id': current_rid, **base})
    return {'outcome': 'published', 'release_id': rid}


def previous_release(root: Path):
    """Последний ранее принятый выпуск, отличный от текущего (цель отката)."""
    root = Path(root)
    current = current_release_id(root)
    for record in reversed(publication_events(root)):
        if record['release_id'] != current:
            return record['release_id']
    return None


def load_stored_release(root: Path, rid: str) -> tuple:
    store = Path(root) / RELEASES / rid
    if not store.is_dir():
        raise ValueError(f'Нет сохранённого выпуска {rid}')
    manifest = json.loads((store / 'manifest.json').read_text(encoding='utf-8'))
    report = json.loads((store / 'report.json').read_text(encoding='utf-8'))
    if release.release_id(manifest, report) != rid:
        raise ValueError('Сохранённый выпуск изменён: release_id не совпадает')
    return manifest, report, str(store / 'report.json')
