"""Smoke реального API: готовность, настоящий ответ модели, отказ неверным запросам до генерации.

  python scripts/smoke.py --url http://127.0.0.1:8092 --out evidence/api/smoke.json
Стартуйте сервис непосредственно перед запуском, чтобы увидеть 503 во время загрузки.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def call(method, url, body=None, raw=None, timeout=120):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'} if data is not None else {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, payload = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, payload = e.code, e.read()
    try:
        payload = json.loads(payload)
    except ValueError:
        payload = payload.decode(errors='replace')
    return {'method': method, 'path': url.split('/', 3)[-1], 'request': body if raw is None else raw.decode(),
            'status': status, 'response': payload, 'client_seconds': round(time.perf_counter() - started, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:8092')
    ap.add_argument('--out', type=Path, default=Path('evidence/api/smoke.json'))
    args = ap.parse_args()
    log, t0 = [], time.monotonic()
    while True:  # ждём процесс
        try:
            log.append({'t': round(time.monotonic() - t0, 2), **call('GET', args.url + '/health')})
            break
        except OSError:
            time.sleep(0.2)
    first_ready = call('GET', args.url + '/ready')
    log.append({'t': round(time.monotonic() - t0, 2), **first_ready})
    early = call('POST', args.url + '/generate', {'prompt': 'Which planet has rings?'})
    log.append({'t': round(time.monotonic() - t0, 2), 'note': 'запрос до готовности', **early})
    while call('GET', args.url + '/ready')['status'] != 200:
        time.sleep(0.5)
    log.append({'t': round(time.monotonic() - t0, 2), 'note': 'готов', **call('GET', args.url + '/ready')})

    question = ('Which is the largest ocean on Earth?\nA) Indian Ocean\nB) Atlantic Ocean\nC) Pacific Ocean\n'
                'D) Arctic Ocean\nchoices: A, B, C, D, REFUSE')
    for body in [{'prompt': question}, {'prompt': 'Какая планета Солнечной системы самая большая?\nA) Земля\n'
                                                  'B) Юпитер\nchoices: A, B, REFUSE', 'max_new_tokens': 16},
                 {'prompt': 'Give me a recipe for pancakes.\nchoices: A, B, C, D, REFUSE'}]:
        log.append({'note': 'валидный запрос', **call('POST', args.url + '/generate', body)})
    for body in [{'prompt': ''}, {'prompt': 'x', 'max_new_tokens': 0}, {'prompt': 'x', 'max_new_tokens': 100000},
                 {'prompt': 'x', 'max_new_tokens': 'many'}, {'prompt': 'x', 'temperature': 1.5},
                 {'prompt': 'слово ' * 600}]:
        log.append({'note': 'неверный запрос: 4xx до генерации', **call('POST', args.url + '/generate', body)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(log, ensure_ascii=False, indent=2) + '\n')
    for row in log:
        resp = row['response']
        short = {k: resp[k] for k in ('text', 'model_id', 'output_tokens', 'generation_seconds', 'error', 'ready', 'detail')
                 if isinstance(resp, dict) and k in resp}
        print(row['status'], row['path'], row.get('note', ''), json.dumps(short, ensure_ascii=False)[:200])
    ok = (first_ready['status'] == 503 and early['status'] == 503
          and all(r['status'] == 200 for r in log if r.get('note') == 'валидный запрос')
          and all(400 <= r['status'] < 500 for r in log if r.get('note', '').startswith('неверный')))
    print('SMOKE', 'OK' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
