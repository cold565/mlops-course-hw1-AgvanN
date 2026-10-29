"""CLI стенда STUB ONLY. Интерфейс совместим с исходным прототипом; стадии выполняет Prefect-flow.

  python lab.py --workspace .lab init
  python lab.py --workspace .lab run [--manifest manifest-B.json] [--report report-B.json]
                                     [--fail-after-prepare] [--fail-after-pointer]
  python lab.py --workspace .lab rollback      # вернуть предыдущий принятый выпуск
  python lab.py --workspace .lab status

Коды выхода: 0 успех; 2 отказ (отчёт/схема/хеши); 3 управляемый сбой; 1 прочая ошибка.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import ledger
import release


def canonical(value):
    return release.canonical(value)


def digest(data):
    return release.sha256_bytes(data)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    release.atomic_write_json(Path(path), value)


def initialize(root: Path) -> None:
    if root.exists():
        raise FileExistsError('Рабочий каталог уже существует. Выберите новый --workspace.')
    root.mkdir(parents=True)
    runtime = {'base_revision': 'fixture-only', 'tokenizer_sha256': 'a' * 64,
               'template_sha256': 'b' * 64, 'generation': {'max_new_tokens': 32, 'do_sample': False},
               'dtype': 'float32'}
    for version in ['A', 'B']:
        artifact = root / f'model-{version}.txt'
        artifact.write_text(f'STUB ONLY — version {version}\n', encoding='utf-8')
        files = {artifact.name: digest(artifact.read_bytes())}
        m = {'schema_version': 1, 'model_id': f'fixture-{version}', 'artifact_uri': artifact.name,
             'model_sha256': digest(canonical(files)), 'protocol_sha256': 'd' * 64, 'golden_sha256': 'e' * 64,
             'runtime_sha256': digest(canonical(runtime)), 'runtime': runtime, 'file_hashes': files}
        report = {k: m[k] for k in ['model_id', 'model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256']}
        report.update(status='complete', passed=True, evaluation_run_id=f'fixture-eval-{version}',
                      metrics={'accuracy': .8, 'format_rate': 1.0}, synthetic=True)
        write(root / f'manifest-{version}.json', m)
        write(root / f'report-{version}.json', report)
    report = read(root / 'report-B.json'); report['status'] = 'running'; report['passed'] = 'false'
    write(root / 'report-incomplete.json', report)
    report = read(root / 'report-B.json'); report['passed'] = False
    write(root / 'report-reject.json', report)
    write(root / 'config.json', {'tokenizer_revision': 'r1', 'template_sha256': 'b' * 64,
                                 'max_length': 128, 'code_revision': 'prepare-v1'})
    (root / 'input.txt').write_text('Первый пример\nВторой пример\n', encoding='utf-8')
    # Начальная версия A проходит ту же проверку и попадает в журнал (нужно для отката).
    ledger.publish_release(root, read(root / 'manifest-A.json'), read(root / 'report-A.json'),
                           'report-A.json', event='bootstrap')
    print('Создан синтетический стенд:', root)


def _prefect_env(root: Path) -> None:
    """Результаты задач (кеш) и база Prefect живут в workspace и переживают процесс."""
    root = root.resolve()
    os.environ.setdefault('PREFECT_HOME', str(root / '.prefect'))
    os.environ.setdefault('PREFECT_LOCAL_STORAGE_PATH', str(root / '.prefect' / 'results'))
    os.environ.setdefault('PREFECT_LOGGING_LEVEL', 'INFO')


def run(root: Path, report_name: str, fail_after_prepare: bool = False, *, manifest_name: str = 'manifest-B.json',
        fail_after_pointer: bool = False, event: str = 'publish') -> dict:
    _prefect_env(root)
    from flow import release_flow
    return release_flow(str(root.resolve()), manifest=manifest_name, report=report_name,
                        fail_after_prepare=fail_after_prepare, fail_after_pointer=fail_after_pointer, event=event)


def rollback(root: Path, fail_after_prepare: bool = False) -> dict:
    target = ledger.previous_release(root)
    if target is None:
        raise ValueError('Нет ранее принятого выпуска для отката')
    store = Path(ledger.RELEASES) / target
    print('ROLLBACK_TO', target, flush=True)
    return run(root, str(store / 'report.json'), fail_after_prepare,
               manifest_name=str(store / 'manifest.json'), event='rollback')


def status(root: Path) -> None:
    pointer = ledger.read_pointer(root)
    print(json.dumps({'model_id': pointer and pointer['model_id'],
                      'release_id': ledger.current_release_id(root),
                      'accepted_sha256': ledger.pointer_sha256(root),
                      'publications': len(ledger.publication_events(root)),
                      'attempts': len(ledger.read_jsonl(root / ledger.ATTEMPTS))}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path('.lab'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    run_parser = sub.add_parser('run')
    run_parser.add_argument('--report', default='report-B.json')
    run_parser.add_argument('--manifest', default='manifest-B.json')
    run_parser.add_argument('--fail-after-prepare', action='store_true')
    run_parser.add_argument('--fail-after-pointer', action='store_true')
    sub.add_parser('rollback')
    sub.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize(args.workspace)
        elif args.command == 'run':
            run(args.workspace, args.report, args.fail_after_prepare, manifest_name=args.manifest,
                fail_after_pointer=args.fail_after_pointer)
        elif args.command == 'rollback':
            rollback(args.workspace)
        else:
            status(args.workspace)
    except ValueError as exc:
        print('REJECTED:', exc, file=sys.stderr, flush=True)
        return 2
    except ledger.InjectedFailure as exc:
        print('INJECTED_FAILURE:', exc, file=sys.stderr, flush=True)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
