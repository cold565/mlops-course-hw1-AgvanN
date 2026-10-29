"""Prefect-flow выпуска для стенда STUB ONLY (тот же порядок стадий, что у реальной модели).

подготовка входа -> получение/проверка артефакта -> проверка отчёта допуска -> публикация.
Между задачами передаются только небольшие описания: пути, хеши, ID, версии.
"""
import json
import os
import uuid
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.context import get_run_context

import artifacts
import ledger
import release

PREPARE_CODE = 'lines-v2'  # версия кода подготовки стенда, входит в результат


def _cache_key(_context, parameters) -> str:
    """Ключ = SHA-256 {байты входа, весь config подготовки}. Не путь и не mtime."""
    return 'prepare-' + release.cache_key(Path(parameters['input_path']), parameters['config'])


def is_transient(_task, _task_run, state) -> bool:
    """Повторять только временные сбои ввода-вывода/сети. Схема и качество повтором не чинятся."""
    try:
        state.result()
    except (ConnectionError, TimeoutError):
        return True
    except Exception:
        return False
    return False


@task(name='prepare-input', persist_result=True, cache_key_fn=_cache_key)
def prepare_input(input_path: str, config: dict, out_dir: str) -> dict:
    """Проверка и «токенизация» строк стенда; результат сохраняется на диск и переживает процесс."""
    payload = Path(input_path).read_bytes()
    text = payload.decode('utf-8')  # не UTF-8 -> ошибка схемы, не повторяется
    rows = [line for line in text.splitlines() if line.strip()]
    if not rows:
        raise ValueError('Пустой вход подготовки')
    max_length = config['max_length']
    tokenized = [line.split() for line in rows]
    too_long = [i for i, tokens in enumerate(tokenized) if len(tokens) > max_length]
    if too_long:
        raise ValueError(f'Строки длиннее max_length={max_length}: {too_long[:5]}')
    key = release.cache_key(Path(input_path), config)
    result = {'input_sha256': release.sha256_bytes(payload), 'rows': len(rows),
              'tokens': sum(map(len, tokenized)), 'config': config, 'code': PREPARE_CODE}
    out = Path(out_dir) / f'{key}.json'
    release.atomic_write_json(out, result)
    print('PREPARED', result['input_sha256'], flush=True)
    get_run_logger().info('Подготовка выполнена: %s строк, ключ %s', len(rows), key[:12])
    return {'uri': str(out), 'sha256': release.sha256_file(out), 'input_sha256': result['input_sha256'],
            'rows': len(rows), 'cache_key': key}


@task(name='fetch-artifact', retries=2, retry_delay_seconds=1, retry_condition_fn=is_transient)
def fetch_artifact(manifest_path: str, workspace: str) -> dict:
    """Получить описание артефакта и пересчитать хеши его файлов. Веса не возвращаются."""
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    root = artifacts.artifact_root(manifest, Path(workspace))
    verified = release.verify_artifact_files(manifest, root)
    return {'manifest_uri': manifest_path, 'manifest_sha256': release.sha256_file(Path(manifest_path)),
            'model_id': manifest['model_id'], 'artifact_uri': manifest['artifact_uri'],
            'model_sha256': manifest['model_sha256'], 'runtime_sha256': manifest['runtime_sha256'],
            'files_verified': len(verified)}


@task(name='check-gate-report')
def check_gate_report(report_path: str, artifact: dict) -> dict:
    """Решение допуска: полный PASS-отчёт, связанный с этим артефактом и runtime, иначе отказ."""
    manifest = json.loads(Path(artifact['manifest_uri']).read_text(encoding='utf-8'))
    report = json.loads(Path(report_path).read_text(encoding='utf-8'))
    release.validate_release(manifest, report)
    return {'report_uri': report_path, 'report_sha256': release.sha256_json(report),
            'evaluation_run_id': report['evaluation_run_id'], 'passed': True}


@task(name='publish-release')
def publish_release(workspace: str, artifact: dict, gate: dict, prepared: dict,
                    event: str, fail_after_pointer: bool) -> dict:
    manifest = json.loads(Path(artifact['manifest_uri']).read_text(encoding='utf-8'))
    report = json.loads(Path(gate['report_uri']).read_text(encoding='utf-8'))
    if release.sha256_json(report) != gate['report_sha256']:
        raise ValueError('Отчёт изменён после проверки допуска')
    # Перед выпуском ещё раз пересчитать хеши файлов: между стадиями их могли подменить.
    release.verify_artifact_files(manifest, artifacts.artifact_root(manifest, Path(workspace)))
    return ledger.publish_release(Path(workspace), manifest, report,
                                  os.path.relpath(gate['report_uri'], workspace), event=event,
                                  context={'prepared_sha256': prepared['input_sha256']},
                                  fail_after_pointer=fail_after_pointer)


@flow(name='release-lab', persist_result=True)
def release_flow(workspace: str, manifest: str = 'manifest-B.json', report: str = 'report-B.json',
                 fail_after_prepare: bool = False, fail_after_pointer: bool = False,
                 event: str = 'publish') -> dict:
    ws = Path(workspace).resolve()
    run_id = str(get_run_context().flow_run.id)
    attempt = {'attempt_id': uuid.uuid4().hex, 'flow_run_id': run_id, 'manifest': manifest,
               'report': report, 'event': event, 'pointer_sha256_before': ledger.pointer_sha256(ws),
               'tasks': {}}
    try:
        config = json.loads((ws / 'config.json').read_text(encoding='utf-8'))
        state = prepare_input(str(ws / 'input.txt'), config, str(ws / 'prepared'), return_state=True)
        prepared = state.result()
        if state.name == 'Cached':
            stored = Path(prepared['uri'])
            if not stored.is_file() or release.sha256_file(stored) != prepared['sha256']:
                # запись кеша есть, а файла результата нет/изменён: пересчитать
                state = prepare_input.with_options(refresh_cache=True)(
                    str(ws / 'input.txt'), config, str(ws / 'prepared'), return_state=True)
                prepared = state.result()
            else:
                print('CACHE_HIT', prepared['input_sha256'], flush=True)
        attempt['tasks']['prepare'] = state.name
        attempt['prepared'] = prepared
        if fail_after_prepare:
            raise ledger.InjectedFailure('Инъекция сбоя после подготовки')

        state = fetch_artifact(str(ws / manifest), str(ws), return_state=True)
        attempt['tasks']['fetch'] = state.name
        artifact = state.result()
        state = check_gate_report(str(ws / report), artifact, return_state=True)
        attempt['tasks']['gate'] = state.name
        gate = state.result()
        state = publish_release(str(ws), artifact, gate, prepared, event, fail_after_pointer, return_state=True)
        attempt['tasks']['publish'] = state.name
        result = state.result()
        attempt.update(outcome=result['outcome'], release_id=result['release_id'])
        print(result['outcome'].upper(), artifact['model_id'], result['release_id'], flush=True)
        return result
    except ValueError as exc:
        attempt.update(outcome='rejected', reason=str(exc))
        raise
    except Exception as exc:
        attempt.update(outcome='failed', reason=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        attempt['pointer_sha256_after'] = ledger.pointer_sha256(ws)
        ledger.record_attempt(ws, attempt)
