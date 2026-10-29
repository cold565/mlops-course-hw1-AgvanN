# ДЗ 8+9+10: процесс выпуска, проверки и API

Исправленный прототип выпуска модели: Prefect-flow, gate ДЗ 6+7, атомарный указатель с журналом,
FastAPI-сервис с внедряемым backend (STUB или Qwen3-0.6B). Отчёт: [REPORT.md](REPORT.md),
дефекты: [defects.csv](defects.csv). Условие задания: `../homework8-9-10.html`, `README.txt`, `INCIDENTS.txt`.

## Устройство

| Файл | Роль |
|---|---|
| `release.py` | ядро без зависимостей: `cache_key`, `validate_release`, атомарный `publish`, хеши файлов артефакта |
| `ledger.py` | `accepted.json` + `publications.jsonl` (только выпуски) + `attempts.jsonl` (все попытки), `release_id`, восстановление, откат |
| `flow.py`, `lab.py` | Prefect-flow стенда STUB ONLY; CLI совместим с исходным `lab.py` |
| `model_flow.py`, `model_runtime.py` | Prefect-flow реальной модели: подготовка (кеш) → артефакт → полная оценка (кеш) → `quality_gate` ДЗ 6+7 → выпуск; общий код генерации для оценки и API |
| `api.py`, `backends.py`, `service.py` | FastAPI `/health`, `/ready`, `/generate` (`/debug` только для stub); `StubBackend` и `TransformersBackend` с общим интерфейсом |
| `tests/` | 13 открытых тестов (не изменялись) |
| `tests_integration/` | 7 собственных интеграционных тестов: реальные процессы, HTTP, файловая система, инъекция сбоев |
| `scripts/` | красный/зелёный прогон, опыты стенда, реальный выпуск, smoke API, замер, намеренная поломка |
| `evidence/` | все журналы; `release/` — реальный manifest, отчёты оценки, release-report, указатель и журналы |

## Команды

```bash
uv sync --frozen                 # быстрые проверки: Prefect + FastAPI, без torch и весов
make check                       # 13 открытых + 7 интеграционных тестов (~1.5 мин)
make red-green                   # новые тесты на исходном коммите (красный) и текущем (зелёный)
make lab-demo                    # опыты INCIDENTS.txt на стенде, лог evidence/lab/lab-demo.log

# стенд вручную
.venv/bin/python lab.py --workspace .lab init
.venv/bin/python lab.py --workspace .lab run --fail-after-prepare   # exit 3
.venv/bin/python lab.py --workspace .lab run                        # CACHE_HIT, PUBLISHED
.venv/bin/python lab.py --workspace .lab rollback
.venv/bin/python service.py --workspace .lab --port 8091 --warmup-seconds 10

# реальная модель (нужны uv sync --frozen --extra model, кеш HF Qwen/Qwen3-0.6B@c1899de…, ДЗ 6+7 рядом)
make release-model               # HW67_ROOT=../../mlops26-hw6-7/hw67-broken python model_flow.py --role baseline
make serve-model                 # API на 127.0.0.1:8092 по release/accepted.json
make smoke-model && make bench
```

Коды выхода `lab.py` и `model_flow.py`: 0 успех, 2 отказ (отчёт/схема/хеши/gate), 3 управляемый сбой, 1 прочее.
