"""Prefect-flow выпуска реальной модели (Qwen3-0.6B ДЗ 6+7). Новое обучение не выполняется.

  подготовка входа оценки (кеш) -> получение/проверка артефакта -> полная оценка (кеш)
  -> quality_gate ДЗ 6+7 -> release-report.json -> атомарный accepted.json + журнал

  python model_flow.py --role baseline     # закреплённая база из evaluation/baseline.json ДЗ 6+7
  python model_flow.py --role candidate    # адаптер training run ДЗ 6+7 (нужен экспорт весов)

Между задачами передаются только пути, хеши, ID и версии; веса и тензоры — нет.
"""
import argparse
import contextlib
import inspect
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE_DIR = Path(os.environ.get('HW8910_RELEASE_DIR', HERE / 'release')).resolve()
(RELEASE_DIR / '.prefect').mkdir(parents=True, exist_ok=True)
os.environ.setdefault('PREFECT_HOME', str(RELEASE_DIR / '.prefect'))
os.environ.setdefault('PREFECT_LOCAL_STORAGE_PATH', str(RELEASE_DIR / '.prefect' / 'results'))

from prefect import flow, get_run_logger, task  # noqa: E402
from prefect.context import get_run_context  # noqa: E402

import ledger  # noqa: E402
import model_runtime  # noqa: E402
import release  # noqa: E402

PREPARE_MAX_LENGTH = 512  # тот же лимит входа, что у API (--max-input-tokens)


def rel(path) -> str:
    """Пути в отчётах — относительно проекта, чтобы комплект переносился."""
    return os.path.relpath(Path(path).resolve(), HERE)


def _tag(manifest: dict) -> str:
    """Имя файлов: модель + runtime + протокол/golden — разные опыты не перезаписывают друг друга."""
    return release.sha256_json({k: manifest[k] for k in ('model_sha256', 'runtime_sha256',
                                                          'protocol_sha256', 'golden_sha256')})[:16]


class ArtifactUnavailable(ValueError):
    """Веса не получены: повтор не поможет, выпуск закрыт."""


def hw67_root() -> Path:
    """HW67_ROOT; иначе соседняя папка репозитория курса (../hw6-7) или локальный проект ДЗ 6+7."""
    if os.environ.get('HW67_ROOT'):
        return Path(os.environ['HW67_ROOT']).resolve()
    for candidate in (HERE / '../hw6-7', HERE / '../../mlops26-hw6-7/hw67-broken'):
        if (candidate / 'evaluation/solution.py').is_file():
            return candidate.resolve()
    raise FileNotFoundError('Не найден проект ДЗ 6+7: задайте HW67_ROOT')


def hw67_result(root: Path, name: str) -> Path:
    """metrics/ ДЗ 6+7 не в Git; в репозитории лежит побайтная копия в reports/."""
    path = root / 'metrics' / name
    return path if path.is_file() else root / 'reports' / name


@contextlib.contextmanager
def hw67(root: Path):
    """Код оценки ДЗ 6+7 читает относительные пути: временно работаем из его корня."""
    old = os.getcwd()
    sys.path.insert(0, str(root))
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(old)
        sys.path.remove(str(root))


def load_protocol(root: Path) -> dict:
    with hw67(root):
        from evaluation import cli
        from evaluation.contract import fingerprint
        config, rows, behavioral, baseline_spec, proof, protocol_hash = cli.inputs()
        return {'config': config, 'rows': rows, 'behavioral': behavioral, 'baseline_spec': baseline_spec,
                'leakage': proof, 'protocol_sha256': protocol_hash, 'golden_sha256': fingerprint(rows),
                'baseline_spec_sha256': fingerprint(baseline_spec),
                'golden_path': str(root / config['golden']), 'behavioral_path': str(root / config['behavioral'])}


def hw67_dtype(root: Path) -> str:
    for line in (root / 'params.yaml').read_text().splitlines():
        if line.strip().startswith('dtype:'):
            return line.split(':', 1)[1].split('#')[0].strip()
    raise ValueError('params.yaml ДЗ 6+7 не содержит model.dtype')


# ------------------------------------------------------------------ задачи

@task(name='describe-runtime')
def describe_runtime(root: str, device: str) -> dict:
    protocol = load_protocol(Path(root))
    spec = protocol['baseline_spec']
    base_root = model_runtime.base_root_for({'base_model': spec['name'], 'base_revision': spec['revision']})
    return model_runtime.build_runtime(
        base_root, spec['name'], spec['revision'], system=protocol['config']['system'],
        max_new_tokens=protocol['config']['max_new_tokens'], enable_thinking=protocol['config']['enable_thinking'],
        dtype=hw67_dtype(Path(root)), device=model_runtime.resolve_device(device), adapter=False)


def _prepare_key(_ctx, p) -> str:
    return 'eval-inputs-' + release.cache_key(Path(p['golden_path']), p['config'])


@task(name='prepare-eval-inputs', persist_result=True, cache_key_fn=_prepare_key)
def prepare_eval_inputs(golden_path: str, behavioral_path: str, config: dict, out_dir: str) -> dict:
    """Проверка и токенизация golden+behavioral тем же шаблоном и system, что у API."""
    if release.sha256_file(Path(behavioral_path)) != config['behavioral_sha256']:
        raise ValueError('behavioral.jsonl изменился после построения ключа')
    tokenizer = model_runtime.load_tokenizer(model_runtime.base_root_for(config))
    rows = [json.loads(l) for path in (golden_path, behavioral_path)
            for l in Path(path).read_text(encoding='utf-8').splitlines() if l.strip()]
    ids = {}
    for row in rows:
        if not isinstance(row.get('input'), str) or not row['input'].strip() or row['id'] in ids:
            raise ValueError(f"Некорректная строка оценки: {row.get('id')}")
        text = model_runtime.render_prompt(tokenizer, config['system'], row['input'], config['enable_thinking'])
        ids[row['id']] = tokenizer(text)['input_ids']
    lengths = {k: len(v) for k, v in ids.items()}
    if max(lengths.values()) > config['max_length']:
        raise ValueError(f"Вход длиннее max_length={config['max_length']}")
    key = release.cache_key(Path(golden_path), config)
    out = Path(out_dir) / f'{key}.json'
    release.atomic_write_json(out, {'cache_key': key, 'config': config, 'input_ids': ids})
    print('PREPARED', key, len(ids), 'rows', flush=True)
    return {'uri': str(out), 'sha256': release.sha256_file(out), 'rows': len(ids),
            'max_tokens': max(lengths.values()), 'cache_key': key}


@task(name='fetch-artifact', retries=2, retry_delay_seconds=2,
      retry_condition_fn=lambda t, r, s: _transient(s))
def fetch_artifact(role: str, root: str, runtime: dict, protocol_sha256: str, golden_sha256: str,
                   out_dir: str) -> dict:
    """Читает готовый результат ДЗ 6+7 (не обучает): закреплённую базу или адаптер training run."""
    root = Path(root)
    base_root = model_runtime.base_root_for(runtime)
    if role == 'baseline':
        spec = json.loads((root / 'evaluation/baseline.json').read_text())
        files = release.hash_files(base_root, model_runtime.BASE_FILES)
        manifest = {'model_id': f"{spec['name']}@{spec['revision'][:12]}",
                    'artifact_uri': f"hf://{spec['name']}@{spec['revision']}",
                    'provenance': {'kind': 'base', 'source': 'ДЗ 6+7 evaluation/baseline.json', 'spec': spec}}
    else:
        # Принятая (champion) версия ДЗ 6+7 с её evaluation run; candidate-model.json там позже
        # перезаписан другим run id при повторной проверке хранения, поэтому не используется.
        spec = json.loads(hw67_result(root, 'released.json').read_text())['model']
        adapter_dir = Path(os.environ.get('HW8910_ADAPTER_DIR', HERE / 'models' / spec['run_id']))
        missing = [f for f in model_runtime.ADAPTER_FILES if not (adapter_dir / f).is_file()]
        if missing:
            raise ArtifactUnavailable(f"Нет файлов адаптера {spec['uri']} (run {spec['run_id']}) в {adapter_dir}: "
                                      f'{missing}. Экспортируйте артефакт MLflow; новое обучение не выполняется')
        files = release.hash_files(adapter_dir, model_runtime.ADAPTER_FILES)
        runtime = {**runtime, 'adapter': True}
        manifest = {'model_id': f"{spec['name']}/v{spec['version']}@{spec['run_id'][:12]}",
                    'artifact_uri': f'file://{adapter_dir}',
                    'base_file_hashes': release.hash_files(base_root, model_runtime.BASE_FILES),
                    'provenance': {'kind': 'adapter', 'source': 'ДЗ 6+7 metrics/released.json', 'spec': spec}}
    manifest.update(schema_version=1, file_hashes=files, model_sha256=release.files_digest(files),
                    protocol_sha256=protocol_sha256, golden_sha256=golden_sha256,
                    runtime=runtime, runtime_sha256=release.sha256_json(runtime))
    out = Path(out_dir) / f"manifest-{role}-{_tag(manifest)}.json"
    release.atomic_write_json(out, manifest)
    return {'manifest_uri': str(out), 'manifest_sha256': release.sha256_file(out), 'model_id': manifest['model_id'],
            'model_sha256': manifest['model_sha256'], 'runtime_sha256': manifest['runtime_sha256'], 'role': role}


def _transient(state) -> bool:
    try:
        state.result()
    except (ConnectionError, TimeoutError):
        return True
    except Exception:
        return False
    return False


def _eval_key(_ctx, p) -> str:
    a, prep = p['artifact'], p['prepared']
    return 'full-eval-' + release.sha256_json({'model_sha256': a['model_sha256'], 'runtime_sha256': a['runtime_sha256'],
                                               'protocol_sha256': p['protocol_sha256'],
                                               'golden_sha256': p['golden_sha256'], 'prepared': prep['sha256']})


@task(name='full-evaluation', persist_result=True, cache_key_fn=_eval_key)
def full_evaluation(artifact: dict, prepared: dict, root: str, protocol_sha256: str, golden_sha256: str,
                    out_dir: str) -> dict:
    """Полная оценка golden+behavioral кодом score ДЗ 6+7. Повторяется только при смене модели,
    runtime, протокола или данных оценки (ключ кеша)."""
    import time
    logger = get_run_logger()
    manifest = json.loads(Path(artifact['manifest_uri']).read_text())
    protocol = load_protocol(Path(root))
    if (protocol['protocol_sha256'], protocol['golden_sha256']) != (protocol_sha256, golden_sha256):
        raise ValueError('Протокол/golden изменились во время выполнения')
    inputs = json.loads(Path(prepared['uri']).read_text())['input_ids']
    generator = model_runtime.Generator.from_manifest(manifest, HERE, verify=True)
    outputs = []
    for row in protocol['rows'] + protocol['behavioral']:
        started = time.monotonic()
        raw, error = '', None
        try:
            raw = generator.generate_ids(inputs[row['id']], generator.max_new_tokens)['text']
        except Exception as exc:  # ошибка генерации не скрывается: попадёт в отчёт и закроет gate
            error = f'{type(exc).__name__}: {exc}'
        outputs.append({'id': row['id'], 'raw': raw, 'error': error, 'latency_seconds': time.monotonic() - started})
    run_id = f'prefect-{get_run_context().task_run.flow_run_id}'
    with hw67(Path(root)):
        from evaluation.contract import fingerprint
        from evaluation.solution import score
        by_id = {o['id']: o for o in outputs}
        report = score(protocol['rows'], [by_id[r['id']] for r in protocol['rows']])
        report.update(status='complete', model=manifest['provenance']['spec'], protocol_sha256=protocol_sha256,
                      golden_sha256=golden_sha256, baseline_spec_sha256=protocol['baseline_spec_sha256'],
                      behavioral=score(protocol['behavioral'], [by_id[r['id']] for r in protocol['behavioral']]),
                      leakage=protocol['leakage'], outputs_sha256=fingerprint(outputs),
                      evaluation_run_id=run_id, model_id=manifest['model_id'],
                      model_sha256=manifest['model_sha256'], runtime_sha256=manifest['runtime_sha256'],
                      runtime=manifest['runtime'])
    folder = Path(out_dir) / f"{artifact['role']}-{_tag(manifest)}"
    release.atomic_write_json(folder / 'outputs.json', outputs)
    release.atomic_write_json(folder / 'eval.json', report)
    o = report['overall']
    logger.info('Оценка %s: %s/%s, format %s/%s', manifest['model_id'], o['correct'], o['n'], o['valid'], o['n'])
    print('EVALUATED', manifest['model_id'], f"{o['correct']}/{o['n']}", flush=True)
    return {'report_uri': str(folder / 'eval.json'), 'report_sha256': release.sha256_file(folder / 'eval.json'),
            'outputs_uri': str(folder / 'outputs.json'), 'outputs_sha256': release.sha256_file(folder / 'outputs.json'),
            'evaluation_run_id': run_id}


def _checked_eval(root: Path, eval_path: Path, outputs_path: Path, protocol: dict) -> dict:
    """Как current_reports ДЗ 6+7: отчёт пересчитывается из сырых ответов и их хеша."""
    with hw67(root):
        from evaluation.contract import fingerprint
        from evaluation.solution import score
        report = json.loads(eval_path.read_text())
        outputs = json.loads(outputs_path.read_text())
        if fingerprint(outputs) != report['outputs_sha256']:
            raise ValueError(f'{eval_path.name}: сырые ответы изменены после оценки')
        by_id = {o['id']: o for o in outputs}
        if set(by_id) != {r['id'] for r in protocol['rows'] + protocol['behavioral']}:
            raise ValueError(f'{eval_path.name}: id сырых ответов не совпадают с протоколом')
        again = score(protocol['rows'], [by_id[r['id']] for r in protocol['rows']])
        if any(report[k] != again[k] for k in ('overall', 'slices', 'per_item')):
            raise ValueError(f'{eval_path.name}: метрики не соответствуют сырым ответам')
        if report['behavioral'] != score(protocol['behavioral'], [by_id[r['id']] for r in protocol['behavioral']]):
            raise ValueError(f'{eval_path.name}: behavioral не соответствует ответам')
        for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
            if report[key] != protocol[key]:
                raise ValueError(f'{eval_path.name}: другой {key}')
    return report


@task(name='quality-gate')
def quality_gate_task(role: str, artifact: dict, evaluation: dict, reference: dict, root: str,
                      out_dir: str) -> dict:
    """Вызов quality_gate ДЗ 6+7 и связанный release-report.json. PASS вручную не пишется."""
    root = Path(root)
    protocol = load_protocol(root)
    manifest = json.loads(Path(artifact['manifest_uri']).read_text())
    candidate = json.loads(Path(evaluation['report_uri']).read_text())
    critical = protocol['config']['critical_slices']
    sources = {'evaluated': {'eval': rel(evaluation['report_uri']), 'eval_sha256': release.sha256_file(Path(evaluation['report_uri'])),
                             'outputs': rel(evaluation['outputs_uri']), 'outputs_sha256': release.sha256_file(Path(evaluation['outputs_uri']))},
               'reference': {'eval': rel(reference['report_uri']), 'eval_sha256': release.sha256_file(Path(reference['report_uri'])),
                             'outputs': rel(reference['outputs_uri']), 'outputs_sha256': release.sha256_file(Path(reference['outputs_uri'])),
                             'evaluation_run_id': json.loads(Path(reference['report_uri']).read_text()).get('evaluation_run_id'),
                             'note': reference['note']},
               'gate_code': {'path': rel(root / 'evaluation/solution.py'),
                             'sha256': release.sha256_file(root / 'evaluation/solution.py')},
               'protocol': {'path': rel(root / 'evaluation/params.json'), 'protocol_sha256': protocol['protocol_sha256']}}
    with hw67(root):
        from evaluation.solution import quality_gate
    try:
        # Оба отчёта пересчитываются из сырых ответов; несовместимость — тоже отказ с отчётом.
        candidate = _checked_eval(root, Path(evaluation['report_uri']), Path(evaluation['outputs_uri']), protocol)
        baseline = _checked_eval(root, Path(reference['report_uri']), Path(reference['outputs_uri']), protocol)
        with hw67(root):
            gate = quality_gate(baseline, candidate, critical)
        passed, reason = gate.get('passed') is True, None
    except ValueError as exc:
        gate, passed, reason = None, False, str(exc)
    o = candidate['overall']
    report = {'status': 'complete', 'passed': passed, 'decision': 'PASS' if passed else 'REJECT', 'reason': reason,
              'role': role, 'evaluation_run_id': evaluation['evaluation_run_id'], 'model_id': manifest['model_id'],
              'model_sha256': manifest['model_sha256'], 'protocol_sha256': manifest['protocol_sha256'],
              'golden_sha256': manifest['golden_sha256'], 'runtime_sha256': manifest['runtime_sha256'],
              'metrics': {'accuracy': o['accuracy'], 'format_rate': o['format_rate'], 'correct': o['correct'],
                          'valid': o['valid'], 'n': o['n'],
                          'critical_slices': {s: candidate['slices'][s]['accuracy'] for s in critical},
                          'behavioral': candidate['behavioral']['overall']['accuracy']},
              'gate': gate, 'sources': sources}
    path = Path(out_dir) / f"release-report-{role}-{evaluation['evaluation_run_id']}.json"
    release.atomic_write_json(path, report)
    release.atomic_write_json(Path(out_dir) / f'release-report-{role}-latest.json', report)
    print('GATE', report['decision'], manifest['model_id'], reason or '', flush=True)
    if not passed:
        raise ValueError(f'Отказ quality_gate: {reason}')
    return {'report_uri': str(path), 'report_sha256': release.sha256_json(report), 'passed': True}


@task(name='publish-release')
def publish_task(artifact: dict, gate: dict, out_dir: str) -> dict:
    manifest = json.loads(Path(artifact['manifest_uri']).read_text())
    report = json.loads(Path(gate['report_uri']).read_text())
    if release.sha256_json(report) != gate['report_sha256']:
        raise ValueError('release-report изменён после gate')
    if release.sha256_file(Path(artifact['manifest_uri'])) != artifact['manifest_sha256']:
        raise ValueError('manifest изменён после получения артефакта')
    # Хеши файлов ещё раз непосредственно перед выпуском.
    if manifest['runtime']['adapter']:
        release.verify_artifact_files(manifest, Path(manifest['artifact_uri'][len('file://'):]))
    else:
        release.verify_artifact_files(manifest, model_runtime.base_root_for(manifest['runtime']))
    return ledger.publish_release(Path(out_dir), manifest, report, rel(gate['report_uri']))


# ------------------------------------------------------------------ flow

@flow(name='release-model', persist_result=True)
def model_release_flow(role: str = 'baseline', root: str = '', device: str = 'auto',
                       fail_after_prepare: bool = False) -> dict:
    root = root or str(hw67_root())
    out = RELEASE_DIR
    run_id = str(get_run_context().flow_run.id)
    attempt = {'flow_run_id': run_id, 'role': role, 'pointer_sha256_before': ledger.pointer_sha256(out), 'tasks': {}}
    try:
        protocol = load_protocol(Path(root))
        runtime = describe_runtime(root, device)
        config = {'base_model': runtime['base_model'], 'base_revision': runtime['base_revision'],
                  'tokenizer_sha256': runtime['tokenizer_sha256'], 'template_sha256': runtime['template_sha256'],
                  'system': runtime['generation']['system'], 'enable_thinking': runtime['generation']['enable_thinking'],
                  'behavioral_sha256': release.sha256_file(Path(protocol['behavioral_path'])),
                  'max_length': PREPARE_MAX_LENGTH,
                  'code_revision': release.sha256_bytes((inspect.getsource(prepare_eval_inputs.fn)
                                                         + inspect.getsource(model_runtime.render_prompt)).encode())}
        # base_root_for ждёт ключи runtime
        state = prepare_eval_inputs(protocol['golden_path'], protocol['behavioral_path'], config,
                                    str(out / 'cache/prepared'), return_state=True)
        attempt['tasks']['prepare'] = state.name
        prepared = state.result()
        if state.name == 'Cached':
            print('CACHE_HIT', prepared['cache_key'], flush=True)
        if fail_after_prepare:
            raise ledger.InjectedFailure('Инъекция сбоя после подготовки')

        hashes = (protocol['protocol_sha256'], protocol['golden_sha256'])
        state = fetch_artifact(role, root, runtime, *hashes, str(out / 'manifests'), return_state=True)
        attempt['tasks']['fetch'] = state.name
        artifact = state.result()

        state = full_evaluation(artifact, prepared, root, *hashes, str(out / 'eval'), return_state=True)
        attempt['tasks']['evaluate'] = state.name
        evaluation = state.result()
        if state.name == 'Cached':
            intact = all(Path(evaluation[k]).is_file() and release.sha256_file(Path(evaluation[k])) == evaluation[s]
                         for k, s in (('report_uri', 'report_sha256'), ('outputs_uri', 'outputs_sha256')))
            if not intact:  # запись кеша есть, а файлы оценки пропали/изменены: оценить заново
                print('EVAL_CACHE_STALE', evaluation['report_uri'], flush=True)
                state = full_evaluation.with_options(refresh_cache=True)(
                    artifact, prepared, root, *hashes, str(out / 'eval'), return_state=True)
                evaluation = state.result()
            else:
                print('EVAL_CACHE_HIT', evaluation['evaluation_run_id'], flush=True)

        if role == 'baseline':
            # Тот же базовый spec на runtime ДЗ 6+7: смена runtime не должна ухудшить качество.
            reference = {'report_uri': str(hw67_result(Path(root), 'baseline-eval.json')),
                         'outputs_uri': str(hw67_result(Path(root), 'baseline-outputs.json')),
                         'note': 'baseline ДЗ 6+7 (MLflow evaluation run 0c5e59f3…, CUDA, WSL)'}
        else:
            base_art = fetch_artifact('baseline', root, runtime, *hashes, str(out / 'manifests'))
            base_eval = full_evaluation(base_art, prepared, root, *hashes, str(out / 'eval'))
            reference = {'report_uri': base_eval['report_uri'], 'outputs_uri': base_eval['outputs_uri'],
                         'note': 'baseline на том же runtime'}
        state = quality_gate_task(role, artifact, evaluation, reference, root, str(out / 'reports'), return_state=True)
        attempt['tasks']['gate'] = state.name
        gate = state.result()
        state = publish_task(artifact, gate, str(out), return_state=True)
        attempt['tasks']['publish'] = state.name
        result = state.result()
        attempt.update(outcome=result['outcome'], release_id=result['release_id'], model_id=artifact['model_id'])
        print(result['outcome'].upper(), artifact['model_id'], result['release_id'], flush=True)
        return result
    except ValueError as exc:
        attempt.update(outcome='rejected', reason=str(exc))
        raise
    except Exception as exc:
        attempt.update(outcome='failed', reason=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        attempt['pointer_sha256_after'] = ledger.pointer_sha256(out)
        out.mkdir(parents=True, exist_ok=True)
        ledger.record_attempt(out, attempt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--role', choices=['baseline', 'candidate'], default='baseline')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--fail-after-prepare', action='store_true')
    args = ap.parse_args()
    try:
        model_release_flow(role=args.role, device=args.device, fail_after_prepare=args.fail_after_prepare)
    except ValueError as exc:
        print('REJECTED:', exc, file=sys.stderr, flush=True)
        return 2
    except ledger.InjectedFailure as exc:
        print('INJECTED_FAILURE:', exc, file=sys.stderr, flush=True)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
