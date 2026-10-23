"""Подсчёт качества (score) и правило допуска (quality_gate) для ДЗ 6+7.

Контракт: EVALUATION.md и tests/test_eval.py.
"""
import json
import math

METRIC_KEYS = ('n', 'correct', 'valid', 'errors', 'accuracy', 'format_rate')
COMPAT_KEYS = ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256')
MIN_GOLDEN = 50
MIN_SLICES = 3
MIN_BEHAVIORAL = 3


def _reject_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f'non-standard JSON constant {name}')


def parse_answer(raw, choices):
    """Строгий разбор: JSON-объект с единственным полем answer, строка из choices.

    Возвращает метку или None. Markdown-обёртки, лишние поля, дубли ключей,
    NaN/Infinity и текст вокруг JSON считаются нарушением формата.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or set(value) != {'answer'}:
        return None
    answer = value['answer']
    if not isinstance(answer, str) or answer not in choices:
        return None
    return answer


def _summary(items):
    n = len(items)
    correct = sum(1 for i in items if i['correct'])
    valid = sum(1 for i in items if i['valid'])
    errors = sum(1 for i in items if i['error'] is not None)
    return {'n': n, 'correct': correct, 'valid': valid, 'errors': errors,
            'accuracy': correct / n, 'format_rate': valid / n}


def score(rows, outputs):
    """Сопоставить ответы заданиям по id и посчитать overall, slices, per_item.

    Знаменатель всегда равен числу заданий: пустые, невалидные ответы и
    ошибки генерации дают нулевой результат, но не исчезают.
    """
    rows, outputs = list(rows), list(outputs)
    if not rows:
        raise ValueError('Пустой набор заданий')
    row_ids = [r['id'] for r in rows]
    out_ids = [o['id'] for o in outputs]
    if len(row_ids) != len(set(row_ids)):
        raise ValueError('Дубли id в заданиях')
    if len(out_ids) != len(set(out_ids)):
        raise ValueError('Дубли id в ответах')
    if set(row_ids) != set(out_ids):
        missing = sorted(set(row_ids) - set(out_ids))[:5]
        extra = sorted(set(out_ids) - set(row_ids))[:5]
        raise ValueError(f'id ответов не совпадают с заданиями: нет {missing}, лишние {extra}')
    by_id = {o['id']: o for o in outputs}

    per_item = []
    for row in rows:
        out = by_id[row['id']]
        if row.get('expected') not in row.get('choices', []):
            raise ValueError(f"{row['id']}: expected не входит в choices")
        error = out.get('error')
        raw = out.get('raw')
        raw = '' if raw is None else raw
        # Ответ, полученный с ошибкой генерации, не засчитываем даже при валидном тексте.
        parsed = None if error is not None else parse_answer(raw, row['choices'])
        valid = parsed is not None
        per_item.append({'id': row['id'], 'raw': raw, 'parsed': parsed, 'valid': valid,
                         'correct': valid and parsed == row['expected'],
                         'error': error, 'slices': list(row.get('slices', []))})

    groups = {}
    for item in per_item:
        for name in item['slices']:
            groups.setdefault(name, []).append(item)
    return {'overall': _summary(per_item),
            'slices': {name: _summary(groups[name]) for name in sorted(groups)},
            'per_item': per_item}


def _check_block(block, where):
    """Метрики блока конечны, согласованы с counts и не пустые."""
    if not isinstance(block, dict):
        raise ValueError(f'{where}: нет метрик')
    for key in METRIC_KEYS:
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{where}: нет числа {key}')
        if not math.isfinite(value):
            raise ValueError(f'{where}: {key} не конечно')
    n, correct, valid, errors = (block[k] for k in ('n', 'correct', 'valid', 'errors'))
    if any(int(x) != x or x < 0 for x in (n, correct, valid, errors)):
        raise ValueError(f'{where}: counts должны быть целыми неотрицательными')
    if n <= 0:
        raise ValueError(f'{where}: пустой набор')
    if correct > valid or valid > n or errors > n:
        raise ValueError(f'{where}: counts противоречат друг другу')
    if not math.isclose(block['accuracy'], correct / n, abs_tol=1e-12):
        raise ValueError(f'{where}: accuracy не равна correct/n')
    if not math.isclose(block['format_rate'], valid / n, abs_tol=1e-12):
        raise ValueError(f'{where}: format_rate не равен valid/n')


def _check_report(report, role):
    if not isinstance(report, dict):
        raise ValueError(f'{role}: отчёт отсутствует')
    if report.get('status') != 'complete':
        raise ValueError(f'{role}: отчёт не полный (status={report.get("status")!r})')
    if not report.get('model'):
        raise ValueError(f'{role}: не указана модель')
    for key in COMPAT_KEYS:
        if not isinstance(report.get(key), str) or not report[key]:
            raise ValueError(f'{role}: нет {key}')
    _check_block(report.get('overall'), f'{role}.overall')
    slices = report.get('slices')
    if not isinstance(slices, dict) or len(slices) < MIN_SLICES:
        raise ValueError(f'{role}: нужно минимум {MIN_SLICES} среза')
    for name, block in slices.items():
        _check_block(block, f'{role}.slices[{name}]')
    items = report.get('per_item')
    if not isinstance(items, list) or len(items) != report['overall']['n']:
        raise ValueError(f'{role}: per_item не соответствует n')
    if report['overall']['n'] < MIN_GOLDEN:
        raise ValueError(f'{role}: golden меньше {MIN_GOLDEN} строк')
    ids = [i.get('id') for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f'{role}: дубли id в per_item')
    # counts overall и срезов должны пересчитываться из per_item
    recount = {'n': len(items), 'correct': sum(bool(i.get('correct')) for i in items),
               'valid': sum(bool(i.get('valid')) for i in items),
               'errors': sum(i.get('error') is not None for i in items)}
    for key, value in recount.items():
        if report['overall'][key] != value:
            raise ValueError(f'{role}: overall.{key} не соответствует per_item')
    for name, block in slices.items():
        members = [i for i in items if name in (i.get('slices') or [])]
        if block['n'] != len(members):
            raise ValueError(f'{role}: срез {name} не соответствует per_item')
    if report['overall']['errors']:
        raise ValueError(f'{role}: ошибки генерации ({report["overall"]["errors"]})')
    behavioral = report.get('behavioral')
    if not isinstance(behavioral, dict):
        raise ValueError(f'{role}: нет behavioral-отчёта')
    _check_block(behavioral.get('overall'), f'{role}.behavioral')


def quality_gate(baseline, candidate, critical_slices):
    """PASS -> {'passed': True, ...}; любая несовместимость или регрессия -> ValueError.

    Допуск ухудшения равен нулю и задан до измерения.
    """
    _check_report(baseline, 'baseline')
    _check_report(candidate, 'candidate')

    for key in COMPAT_KEYS:
        if baseline[key] != candidate[key]:
            raise ValueError(f'Несовместимые отчёты: различается {key}')
    if [i['id'] for i in baseline['per_item']] != [i['id'] for i in candidate['per_item']]:
        raise ValueError('Несовместимые отчёты: различаются id заданий')
    if set(baseline['slices']) != set(candidate['slices']):
        raise ValueError('Несовместимые отчёты: различаются имена срезов')
    for name in baseline['slices']:
        if baseline['slices'][name]['n'] != candidate['slices'][name]['n']:
            raise ValueError(f'Несовместимые отчёты: размер среза {name} различается')

    critical = list(critical_slices or [])
    if not critical:
        raise ValueError('Не задан ни один критический срез')
    for name in critical:
        if name not in candidate['slices']:
            raise ValueError(f'Критический срез {name} отсутствует в отчёте')

    b, c = baseline['overall'], candidate['overall']
    if c['accuracy'] < b['accuracy']:
        raise ValueError(f"Регрессия accuracy: {c['correct']}/{c['n']} < {b['correct']}/{b['n']}")
    if c['format_rate'] < b['format_rate']:
        raise ValueError(f"Регрессия format_rate: {c['valid']}/{c['n']} < {b['valid']}/{b['n']}")
    slice_results = {}
    for name in critical:
        bs, cs = baseline['slices'][name], candidate['slices'][name]
        if cs['accuracy'] < bs['accuracy']:
            raise ValueError(f"Регрессия критического среза {name}: "
                             f"{cs['correct']}/{cs['n']} < {bs['correct']}/{bs['n']}")
        slice_results[name] = {'baseline': f"{bs['correct']}/{bs['n']}",
                               'candidate': f"{cs['correct']}/{cs['n']}"}

    beh = candidate['behavioral']['overall']
    if beh['n'] < MIN_BEHAVIORAL:
        raise ValueError(f'Behavioral: нужно минимум {MIN_BEHAVIORAL} кейса')
    if beh['errors'] or beh['correct'] != beh['n']:
        raise ValueError(f"Behavioral-кейсы кандидата не пройдены: {beh['correct']}/{beh['n']}")

    return {'passed': True,
            'overall': {'baseline': {'accuracy': b['accuracy'], 'format_rate': b['format_rate'],
                                     'correct': b['correct'], 'valid': b['valid'], 'n': b['n']},
                        'candidate': {'accuracy': c['accuracy'], 'format_rate': c['format_rate'],
                                      'correct': c['correct'], 'valid': c['valid'], 'n': c['n']}},
            'critical_slices': slice_results,
            'behavioral': f"{beh['correct']}/{beh['n']}",
            'tolerance': 0.0,
            'protocol_sha256': candidate['protocol_sha256']}
