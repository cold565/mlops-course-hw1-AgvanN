"""Небольшой замер: одни и те же 8 запросов при concurrency=1 и 2 после прогрева.

  python scripts/bench.py --url http://127.0.0.1:8092 --out evidence/bench
Сохраняет каждую попытку (включая ошибки и тайм-ауты) в JSONL/CSV и сводную таблицу.
"""
import argparse
import csv
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = next((p for p in (_ROOT / '../hw6-7/data/eval/golden.jsonl',
                           _ROOT / '../../mlops26-hw6-7/hw67-broken/data/eval/golden.jsonl') if p.is_file()),
              _ROOT / '../hw6-7/data/eval/golden.jsonl')


def post(url, body, timeout):
    req = urllib.request.Request(url + '/generate', data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, payload, error = r.status, json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        status, payload, error = e.code, None, e.read().decode(errors='replace')[:300]
    except Exception as e:  # тайм-аут/обрыв соединения тоже строка журнала
        status, payload, error = None, None, f'{type(e).__name__}: {e}'
    finished = time.perf_counter()
    return started, finished, status, payload, error


def run_batch(url, prompts, concurrency, max_new_tokens, timeout, mode):
    def one(item):
        idx, (row_id, prompt) = item
        started, finished, status, payload, error = post(url, {'prompt': prompt, 'max_new_tokens': max_new_tokens}, timeout)
        p = payload or {}
        return {'mode': mode, 'concurrency': concurrency, 'seq': idx, 'item_id': row_id,
                'request_id': p.get('request_id'), 'status': status, 'ok': status == 200, 'error': error,
                'client_latency_s': finished - started, 'queue_s': p.get('queue_seconds'),
                'generation_s': p.get('generation_seconds'), 'server_total_s': p.get('total_seconds'),
                'output_tokens': p.get('output_tokens'), 'max_new_tokens': max_new_tokens,
                'model_id': p.get('model_id'), 'runtime_sha256': p.get('runtime_sha256'),
                'text': p.get('text'), 't_start': started, 't_end': finished}
    batch_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(one, enumerate(prompts)))
    batch_seconds = time.perf_counter() - batch_start
    for r in rows:
        r['t_start'] -= batch_start
        r['t_end'] -= batch_start
    return rows, batch_seconds


def summary(rows, batch_seconds, concurrency):
    ok = [r for r in rows if r['ok']]
    lat = [r['client_latency_s'] for r in ok]
    return {'concurrency': concurrency, 'n': len(rows), 'ok': len(ok), 'errors': len(rows) - len(ok),
            'batch_s': round(batch_seconds, 3), 'ok_req_per_s': round(len(ok) / batch_seconds, 3),
            'mean_latency_s': round(statistics.mean(lat), 3) if lat else None,
            'max_latency_s': round(max(lat), 3) if lat else None,
            'mean_queue_s': round(statistics.mean(r['queue_s'] for r in ok), 3) if ok else None,
            'mean_generation_s': round(statistics.mean(r['generation_s'] for r in ok), 3) if ok else None,
            'output_tokens': sorted({r['output_tokens'] for r in ok}),
            'error_latencies_s': [round(r['client_latency_s'], 3) for r in rows if not r['ok']],
            'model_ids': sorted({r['model_id'] for r in ok})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:8092')
    ap.add_argument('--out', type=Path, default=Path('evidence/bench'))
    ap.add_argument('--n', type=int, default=8)
    ap.add_argument('--max-new-tokens', type=int, default=32)
    ap.add_argument('--timeout', type=float, default=120)
    ap.add_argument('--golden', type=Path, default=GOLDEN)
    args = ap.parse_args()
    rows = [json.loads(l) for l in args.golden.read_text(encoding='utf-8').splitlines() if l.strip()]
    prompts = [(r['id'], r['input']) for r in rows[:args.n]]
    # Прогрев вне замера: один запрос, результат не входит в таблицу.
    warm = post(args.url, {'prompt': prompts[0][1], 'max_new_tokens': args.max_new_tokens}, args.timeout)
    if warm[2] != 200:
        raise SystemExit(f'Сервис не готов: {warm[2]} {warm[4]}')
    all_rows, table = [], []
    for c in (1, 2):
        batch, seconds = run_batch(args.url, prompts, c, args.max_new_tokens, args.timeout, f'c{c}')
        all_rows += batch
        table.append(summary(batch, seconds, c))
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / 'requests.jsonl', 'w', encoding='utf-8') as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(args.out / 'requests.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=[k for k in all_rows[0] if k != 'text'], extrasaction='ignore')
        w.writeheader()
        w.writerows(all_rows)
    (args.out / 'summary.json').write_text(json.dumps(table, ensure_ascii=False, indent=2) + '\n')
    head = '| concurrency | n | ok | errors | batch, s | ok req/s | mean latency, s | max latency, s | mean queue, s | mean generation, s | output tokens |'
    lines = [head, '|' + '---|' * 11]
    for t in table:
        lines.append(f"| {t['concurrency']} | {t['n']} | {t['ok']} | {t['errors']} | {t['batch_s']} | {t['ok_req_per_s']} | "
                     f"{t['mean_latency_s']} | {t['max_latency_s']} | {t['mean_queue_s']} | {t['mean_generation_s']} | "
                     f"{t['output_tokens']} |")
    (args.out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    print('model_id:', table[0]['model_ids'])


if __name__ == '__main__':
    main()
