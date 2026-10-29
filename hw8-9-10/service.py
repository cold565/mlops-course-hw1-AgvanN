"""Запуск API. Интерфейс совместим с исходным прототипом (только localhost).

Стенд STUB ONLY:   python service.py --workspace .lab --port 8091 --warmup-seconds 10
Реальная модель:   python service.py --backend transformers --pointer release/accepted.json --port 8092
"""
import argparse
from pathlib import Path

import uvicorn

from api import create_app
from backends import make_backend


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, default=Path('.lab'))
    p.add_argument('--pointer', type=Path, help='accepted.json; по умолчанию <workspace>/accepted.json')
    p.add_argument('--backend', choices=['stub', 'transformers'], default='stub')
    p.add_argument('--port', type=int, default=8091)
    p.add_argument('--warmup-seconds', type=float, default=5)
    p.add_argument('--stub-delay', type=float, default=0.01)
    p.add_argument('--max-concurrency', type=int, default=1)
    p.add_argument('--max-queue', type=int, default=8)
    p.add_argument('--max-input-tokens', type=int, default=512)
    p.add_argument('--debug', action=argparse.BooleanOptionalAction, default=None,
                   help='/debug: по умолчанию только для stub')
    args = p.parse_args()
    pointer = args.pointer or args.workspace / 'accepted.json'
    options = {'delay': args.stub_delay} if args.backend == 'stub' else {}
    debug = args.backend == 'stub' if args.debug is None else args.debug
    app = create_app(pointer.resolve(), make_backend(args.backend, **options), warmup_seconds=args.warmup_seconds,
                     debug=debug, max_input_tokens=args.max_input_tokens,
                     max_concurrency=args.max_concurrency, max_queue=args.max_queue)
    label = 'STUB ONLY' if args.backend == 'stub' else 'MODEL'
    print(f'{label}: http://127.0.0.1:{args.port}', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
