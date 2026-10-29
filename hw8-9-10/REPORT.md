# Отчёт ДЗ 8+9+10

Mac (Apple M5, 16 ГБ, MPS), Python 3.12.15, Prefect 3.9.0, FastAPI 0.150.0, uvicorn 0.54.0,
torch 2.14.0 / transformers 5.17.0 / peft 0.21.0 (те же версии, что в ДЗ 6+7). CI выполнялся локально.

## 1. Репозиторий, коммиты, команды

| Коммит | Содержание |
|---|---|
| `4aa9358` | исходный прототип без изменений |
| `497fa99` | исправление 7 причин, Prefect-flow, FastAPI, реальная модель, 7 интеграционных тестов |
| `1126672` | журналы `evidence/`, реальный выпуск `release/`, `defects.csv`, отчёт |

Зависимости закреплены в `pyproject.toml` + `uv.lock`. Быстрый набор (`uv sync --frozen`) содержит только Prefect, FastAPI и uvicorn.
torch, transformers и peft ставятся отдельно: `--extra model`. Команды описаны в [README.md](README.md).

**defects.csv** — 7 независимых причин. Для каждой указаны воспроизведение, ожидаемый и фактический результат, причина, исправление и регрессионный тест.

| # | Причина | Тест |
|---|---|---|
| 1 | `cache_key` хеширует путь, а не байты и config | `test_crash_recovery_uses_cache_and_input_change_invalidates` |
| 2 | `validate_release` проверяет только truthiness `passed` | `test_incomplete_foreign_and_rejected_reports_keep_pointer` |
| 3 | `publish` пишет прямо в целевой файл (не атомарно) | `test_disk_error_during_publish_keeps_previous_bytes` |
| 4 | безусловный append в журнал, идентичность = model_id | `test_repeated_release_publishes_once_and_recovers_after_pointer_crash` |
| 5 | `ready=True` с момента старта | `test_not_ready_until_warmup_finished` |
| 6 | model_id ответа читается из файла указателя, а не из backend | `test_loaded_version_changes_only_after_restart_and_rollback` |
| 7 | тело запроса не валидируется до вызова backend | `test_invalid_requests_rejected_without_backend_call` |

**Собственные интеграционные тесты** (`tests_integration/`, сверх 13 открытых):
- 3 HTTP-теста: сервис запускается отдельным процессом на свободном порту;
- 4 теста процесса выпуска через subprocess, у каждого опыта свой рабочий каталог;
- откат и повторная публикация проверяются в тестах 4 и 6.

Модель в тестах заменена stub, но HTTP, файловая система и границы процессов настоящие. Ошибки записи вносятся
снаружи через `tests_integration/inject.py`: подменяется `io.open`, и код под тестом об инъекции не знает.

Тесты используют только CLI `lab.py`/`service.py` и HTTP-маршруты. Поэтому один и тот же набор
запускается против исходного кода (`HW8910_TARGET=<checkout 4aa9358>`) и против исправленного.

| Лог | Результат |
|---|---|
| `evidence/red-original.log` | исходный прототип: 7/7 тестов FAIL (19 failures с subTest), `exit_code=1` |
| `evidence/green-fixed.log` | исправленная версия: 7/7 OK, `exit_code=0` |
| `evidence/public-tests-original.log` | 13 открытых тестов на исходной версии: 54 failures |

Каждый красный тест падает на утверждении о своём дефекте, а не на импорте или отсутствующей команде:
- `CACHE_HIT 88bbd559…` вместо `PREPARED ec50cb14…`;
- `report-incomplete.json` даёт `exit 0`;
- после ENOSPC в `accepted.json` записана половина манифеста B;
- `2 != 1` событий выпуска B;
- `/ready` отвечает `200` вместо `503`;
- `'fixture-B' != 'fixture-A'`;
- неверные запросы получили `200`, а счётчик вызовов `9 != 0`.

Открытые тесты не изменялись: `git diff 4aa9358 -- tests/` пуст.

## 2. Что переиспользовано из ДЗ 6+7

Используется проект `../../mlops26-hw6-7/hw67-broken` (переменная `HW67_ROOT`). Его код не меняется, он только импортируется.

- Протокол оценки: `evaluation/params.json`, golden 50 + behavioral 3, критические срезы `science` и `four_options`. Хеш протокола `2842154e…` воспроизводится.
- Код допуска: `evaluation/solution.py` (`score`, `quality_gate`, допуск 0), sha256 `6faf323c…`.
- Baseline: `evaluation/baseline.json`, то есть `Qwen/Qwen3-0.6B` на ревизии `c1899de289a04d12100db370d81485cdf75e47ca`.
- Оценка baseline в ДЗ 6+7: MLflow evaluation run `0c5e59f369724c618db7e644cf6b6ffb` (CUDA, WSL), `metrics/baseline-eval.json` и сырые ответы `metrics/baseline-outputs.json`, 43/50.
- Принятый кандидат ДЗ 6+7: `models:/my-qwen-adapter/1`, training run `1eb8479949014e33a8c9934cc247d8e3`. Он прошёл gate вничью, 43/50 (`metrics/released.json`).

**Почему обслуживается baseline.** Веса адаптера лежат в docker-volume MLflow на Windows/WSL-машине, где выполнялось ДЗ 6+7. На этом Mac их нет.
Подставлять другой адаптер (например, из ДЗ 5) или переобучать нельзя: это была бы уже другая модель.
Поэтому `model_flow.py --role candidate` честно закрывается отказом «артефакт недоступен» (`evidence/model/04-candidate-unavailable.log`),
а сервис обслуживает закреплённую baseline с её происхождением, конфигурацией и реальной загрузкой. Путь адаптера реализован:
1. выгрузить артефакт: `mlflow artifacts download -r 1eb84799… -a model`;
2. положить файлы адаптера в `models/1eb8479949014e33a8c9934cc247d8e3/`;
3. запустить `python model_flow.py --role candidate`.

Flow оценит baseline и кандидата на одном runtime, вызовет `quality_gate`, при PASS опубликует новую версию, и сервис нужно перезапустить.
Без весов этот путь не проверялся.

`metrics/candidate-model.json` в ДЗ 6+7 позже перезаписан другим run (`6097c886…`) при повторной проверке хранения.
Поэтому flow берёт спецификацию из `released.json`, где записан оценённый и принятый run.

## 3. Граф задач, кеш, повторы

```
release-model (Prefect flow)
  describe-runtime ─> prepare-eval-inputs [кеш] ─> fetch-artifact [retry: только сетевые] ─> full-evaluation [кеш]
                                                              ─> quality-gate ─┬─ REJECT → release-report (passed=false), accepted.json без изменений
                                                                               └─ PASS → publish-release → accepted.json + publications.jsonl → API (перезапуск)
```

Стенд (`flow.py`) устроен так же: `prepare-input [кеш] → fetch-artifact → check-gate-report → publish-release`.
Между задачами передаются только описания: пути, SHA-256, model_id, run ID и runtime. Веса и тензоры не передаются.
`fetch-artifact` читает готовый результат ДЗ 6+7 (снимок HF на точной ревизии или экспорт адаптера) и **не обучает**.

| Кеш | Ключ | Где хранится |
|---|---|---|
| `prepare-eval-inputs` (токенизация golden+behavioral шаблоном API) | `release.cache_key(golden.jsonl, config)`: байты golden, sha behavioral, base_revision, tokenizer_sha256, template_sha256, system, enable_thinking, max_length=512, code_revision = sha исходника задачи | `release/cache/prepared/<key>.json` + результат Prefect (`persist_result`) в `release/.prefect` |
| `full-evaluation` | model_sha256 + runtime_sha256 + protocol_sha256 + golden_sha256 + хеш подготовки | `release/eval/<role>-<tag>/{eval,outputs}.json` |
| `prepare-input` стенда | `cache_key(input.txt, config.json)` | `<ws>/prepared/`, `<ws>/.prefect` |

При попадании в кеш flow сверяет SHA-256 сохранённого файла. Если файл пропал или изменён, задача выполняется заново (`refresh_cache`).

**Повтор.** Ограниченный повтор (`retries=2`) разрешён только для `ConnectionError` и `TimeoutError` в `fetch-artifact`.
Неверная схема, несовпадение хешей, недоступные веса и отказ gate — это `ValueError`, они не повторяются, а процесс завершается с кодом 2.

**Идентичность результата и попытки.** Результат подготовки определяется ключом из содержимого и настроек, у каждой попытки свой flow run ID.
Выпуск определяется `release_id = SHA-256(model_id, model_sha256, runtime_sha256, protocol, golden, evaluation_run_id, sha отчёта)`,
у попытки своя запись в `attempts.jsonl`.

**Хранение (выбор).**
- `accepted.json` содержит manifest и блок `release` с полями `release_id`, `report_uri` и `report_sha256`. Файл заменяется атомарно: временный файл, `fsync`, `os.replace`.
- `publications.jsonl` хранит только фактические смены версии: `publish`, `rollback`, `bootstrap`.
- `attempts.jsonl` хранит все попытки, включая отказы и сбои.
- `releases/<release_id>/` содержит неизменяемые копии manifest и отчёта для отката.

Сначала пишется указатель, затем событие. Если процесс упал между ними, повтор видит `release_id` в указателе и дописывает событие с `recovered=true`.
Если указатель и журнал согласованы, повтор ничего не пишет. Писатель один, конкуренцию писателей схема не решает.

## 4. Прогоны: успех, сбой, восстановление, изменение входа

Реальная модель (`scripts/model_release_demo.sh`, журналы `evidence/model/`, попытки `release/attempts.jsonl`):

| Шаг | Команда | exit | flow run | Что исполнено / взято из кеша |
|---|---|---|---|---|
| 01 | `model_flow.py --role baseline --fail-after-prepare` | 3 | `9c53f8ab…` | prepare **Completed** (PREPARED b86a8277…, 53 строки), затем инъекция сбоя |
| 02 | `model_flow.py --role baseline` (новый процесс) | 0 | `7315dd9b…` | prepare **Cached**, fetch, полная оценка 43/50, gate PASS, PUBLISHED `a4da27f5…` |
| 03 | то же, повтор | 0 | `0744f3d8…` | prepare **Cached**, evaluate **Cached**, ALREADY_PUBLISHED, событий 1 |
| 04 | `--role candidate` | 2 | `36f2e29d…` | fetch Failed: нет весов адаптера → REJECTED |
| 05 | изменён golden `geo-02` по тому же пути (копия ДЗ 6+7) | 2 | `16ea0bdc…` | prepare **Completed** (новый ключ 1ba4e468…), оценка заново, gate REJECT (другой protocol_sha256) |
| 06 | исходные данные снова | 0 | `9d8c87d0…` | prepare/evaluate **Cached**, ALREADY_PUBLISHED |

`accepted.json` после шага 02: sha256 `fe10895a2a0c9a414bfd4c04f424fc6764f077196d1b341f6248b77e9925b04f`.
После шагов 03–06 хеш тот же, событие публикации одно.

На стенде (`evidence/lab/lab-demo.log`) последовательность такая:
1. сбой после подготовки → `exit 3`;
2. новый процесс → `CACHE_HIT 88bbd559…`, `PUBLISHED fixture-B`;
3. изменение байтов `input.txt` → `PREPARED ec50cb14…`;
4. `max_length 128→256` → `PREPARED`.

Состояния задач Prefect по попыткам: `prepare` = Completed → Cached → Completed → Completed.

Изменение входа стенда не создаёт новый выпуск, потому что модель, runtime и решение допуска те же (`ALREADY_PUBLISHED`).
У реальной модели изменение golden меняет протокол, поэтому требуется новая оценка.

## 5. Gate и безопасная публикация

Связь версий: training run ДЗ 6+7 / закреплённая база → manifest → полная оценка → `quality_gate` ДЗ 6+7 → `release-report.json` → `accepted.json` → загруженная модель API.

- **Manifest** (`release/releases/a4da27f5…/manifest.json`):
  - `model_id = Qwen/Qwen3-0.6B@c1899de289a0`;
  - `artifact_uri = hf://Qwen/Qwen3-0.6B@c1899de…`;
  - `file_hashes` — 7 файлов снимка, включая `model.safetensors` (`f47f7117…`); `model_sha256 = 052d8c0b…`;
  - `protocol_sha256 = 2842154e…`, `golden_sha256 = 474b0d9b…`;
  - `runtime`: base_revision, tokenizer_sha256, template_sha256 (`a55ee1b1…`), system и его хеш, `do_sample=false`, `max_new_tokens=32`, `enable_thinking=false`, `dtype=bfloat16`, `device=mps`, версии библиотек; `runtime_sha256 = 503e69ce…`.
- **Решение.** База на новом runtime (MPS вместо CUDA) прошла `quality_gate(baseline-eval ДЗ 6+7, локальная оценка, critical_slices)`:
  - accuracy 43/50 = 43/50, format 50/50;
  - `science` 16/17, `four_options` 24/30;
  - behavioral 3/3, допуск 0.

  Все 53 сырых ответа совпали с CUDA-прогоном ДЗ 6+7 побайтно.
- **release-report** (`release/reports/release-report-baseline-prefect-7315dd9b….json`) создаётся обвязкой после вызова `quality_gate`, а не вручную.
  Он содержит пути и SHA-256 обоих отчётов оценки и сырых ответов, кода gate и протокола. Перед вызовом gate оба отчёта пересчитываются из сырых ответов.
  Отклонённые отчёты тоже сохраняются (`release-report-baseline-prefect-16ea0bdc….json`, `passed=false`).
- **validate_release** дополнительно сверяет связку manifest и отчёта. Перед публикацией (`publish-release`) и перед загрузкой в API хеши файлов пересчитываются заново.
  API также заново вычисляет runtime своего процесса и отказывается загружаться, если он отличается от runtime оценки.

Отрицательные проверки на стенде (`evidence/lab/lab-demo.log`). До проверок `accepted.json` sha256 `ef076cc3…`, после каждого отказа он тот же:

| Отчёт | Ответ |
|---|---|
| неполный (`status=running`, `passed="false"`) | `REJECTED: Отчёт не завершён`, exit 2 |
| чужой (A для B) | `REJECTED: Отчёт относится к другой модели`, exit 2 |
| отказ (`passed=false`) | `REJECTED: Кандидат не допущен`, exit 2 |
| NaN в accuracy | `REJECTED: metrics.accuracy: нужно конечное число в [0, 1]`, exit 2 |
| ошибка записи (ENOSPC через inject, тест 3) | `exit≠0`, старые байты на месте, `*.tmp` нет |

Ошибку `os.replace` (mock) дополнительно проверяет открытый тест.

**Почему PASS из фикстуры — не оценка модели.** Отчёты стенда (`synthetic=true`) сгенерированы `lab.py init`. Они проверяют только механику связки и отказов.
Это не генерация модели, а их метрики (0.8/1.0) — константы. Реальный допуск — только `release-report` от `model_flow.py`.

## 6. Быстрые проверки и триггеры

`make check`: compileall, 13 открытых тестов и 7 интеграционных, без torch и весов, около 85 с.
- `evidence/check-green.log`: OK, `exit_code=0`.
- `evidence/check-broken.log`: намеренная поломка в `api.py` убирает верхнюю границу `max_new_tokens`. Результат: 3 failures, `exit_code=2`. Затем файл восстановлен, `git diff` пуст.

Скрипт: `scripts/break_demo.sh`.

CI запускался **локально**. Файл `.github/workflows/checks.yml` — это план с теми же командами (`uv sync --frozen`, `make check`). В GitHub Actions он не запускался, branch protection не настроен, поэтому защита merge не заявляется.

| Событие | Действие в проекте |
|---|---|
| Изменился код (сервис, flow, release) | `make check` (быстрые тесты). Если менялась генерация (`model_runtime.py`, шаблон, system, лимиты, dtype, устройство, версии библиотек), меняется `runtime_sha256` и нужна новая оценка: `make release-model`. API откажется грузить manifest с чужим runtime. Правка документации обучения и оценки не требует |
| Изменились данные | Данные оценки (golden/behavioral): `make release-model`. Ключ подготовки и protocol_sha256 меняются, оценка повторяется, gate сверяет совместимость с reference (журнал 05). Данные обучения (train/val ДЗ 4): новое обучение и новый кандидат нужны только при осознанной смене обучающего набора, затем снова оценка. Изменения только в данных оценки обучения не требуют |
| Новый адаптер | Экспорт в `models/<run_id>/` → `python model_flow.py --role candidate`: проверка загрузки и хешей, полная оценка baseline и кандидата на одном runtime, `quality_gate`, при PASS публикация и перезапуск API |

## 7. API

`service.py --backend transformers --pointer release/accepted.json`. Модель загружается в фоне через FastAPI lifespan.
Журналы: `evidence/api/smoke.json` и `smoke.log`.

- `GET /health` → 200 сразу. `GET /ready` → **503** во время загрузки, `/generate` до готовности тоже **503**. Через ~2.8 с после старта `/ready` → 200 с `model_id`, `release_id` и `runtime_sha256`.
- Реальные ответы модели:
  - вопрос про океан → `{"answer": "C"}`, 7 выходных токенов, 0.21 с генерации;
  - вопрос на русском → `{"answer": "B"}`;
  - вопрос не по теме (рецепт) → `{"answer": "REFUSE"}`.

  Каждый ответ содержит `text`, `request_id`, `model_id = Qwen/Qwen3-0.6B@c1899de289a0`, `release_id`, `runtime_sha256 = 503e69ce…`, `input_tokens`, `output_tokens`, `queue_seconds`, `generation_seconds`, `total_seconds`.
- Ограничения:
  - тело ≤ 64 КБ (413);
  - prompt — непустая строка ≤ 4000 символов и ≤ 512 токенов после шаблона;
  - `max_new_tokens` — целое в диапазоне 1..32, где 32 — лимит протокола оценки (по умолчанию тоже 32). Меньший лимит только обрезает ответ: допуск выдан для 32 токенов, поэтому больше 32 нельзя;
  - лишние поля (`temperature` и т. п.) запрещены;
  - одна генерация одновременно (семафор), очередь не больше 8 ожидающих (503).

  Нарушения → **422 до генерации**: пустой prompt, 0, 100000, `"many"`, `temperature`, prompt длиной 1340 токенов.
- `/debug` у реального API → 404, он включается только для stub.
- Смена версии. `accepted.json` читается один раз при старте, поэтому изменение файла не меняет модель в памяти. На стенде это доказано опытом A → B → A (п. 9).
  Для реальной модели после перезапуска загружается текущий manifest: хеши файлов и runtime проверяются перед загрузкой. Второй реально допущенной версии на этой машине нет (см. п. 2).

## 8. Замер

`scripts/bench.py`:
- после загрузки и одного прогревочного запроса (не входит в таблицу) отправляются одни и те же 8 вопросов golden `geo-01…08`, длина промпта 164–179 токенов;
- `max_new_tokens=32`, фактически 7 токенов у каждого ответа: модель останавливается на EOS после `{"answer": "X"}`;
- сервер: одна генерация за раз (семафор), MPS, bf16.

Сырые строки, включая статус, ошибку, клиентскую задержку, очередь, генерацию и model_id: `evidence/bench/requests.jsonl` и `requests.csv`.

| concurrency | n | ok | ошибки | пакет, с | ok req/s | ср. задержка, с | макс. задержка, с | ср. очередь, с | ср. генерация, с |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 8 | 8 | 0 | 1.634 | 4.90 | 0.204 | 0.205 | 0.000 | 0.203 |
| 2 | 8 | 8 | 0 | 1.616 | 4.95 | 0.379 | 0.406 | 0.176 | 0.202 |

**Ожидание и вычисление.** Время генерации одинаково (~0.20 с) в обоих режимах. При concurrency=2 второй запрос ждёт семафор, пока считается первый,
поэтому клиентская задержка почти удваивается (+0.18 с очереди), а пропускная способность не растёт: 4.90 против 4.95 req/s, это шум.
Сериализованный backend превращает параллельность клиентов в очередь. Ошибок и тайм-аутов не было. На 8 запросах выводы о p95 не делаются.

Ответ отдаётся одним JSON, поэтому измерена полная задержка. TTFT и ITL не измерялись.

**Prefill и decode.** Генерация состоит из двух фаз:
- prefill: один проход по всем ~175 токенам промпта, параллельно по токенам, упирается в вычисления;
- decode: 7 последовательных шагов по одному токену, каждый читает все веса и растущий KV-cache, упирается в память.

**KV-cache** Qwen3-0.6B: 28 слоёв × 8 KV-голов × 128 × 2 (K и V) × 2 байта bf16 = 112 КиБ на токен. Это ≈ 20 МиБ на запрос из ~185 токенов и растёт линейно с длиной и числом одновременных запросов.

**Почему это не доказывает скорость SGLang или vLLM.** Последовательный сервис на Transformers обслуживает одну генерацию за раз, без continuous batching, paged KV-cache и переиспользования префикса.
Специализированные движки объединяют decode-шаги многих запросов в один батч, и это меняет и throughput, и задержку под нагрузкой.
Эти числа характеризуют только данную конфигурацию (MPS, batch=1, короткие ответы).

## 9. STUB-опыт A → B → A, повтор после сбоя

Журналы: `evidence/lab/lab-demo.log` (раздел 5–7) и тест `test_loaded_version_changes_only_after_restart_and_rollback`.

| Шаг | Ответ сервиса |
|---|---|
| сервис на A | `model_id=fixture-A`, текст `STUB ONLY: fixture-A [STUB ONLY — version A]` |
| другой процесс публикует B, сервис **не перезапущен** | `accepted.json = fixture-B`, ответ по-прежнему `fixture-A` с текстом версии A |
| перезапуск | `fixture-B`, `version B`, `release_id 591dbf0a…` |
| `lab.py rollback`, затем перезапуск | `fixture-A`, `version A`, `release_id eb66f6c6…` |

`lab.py rollback` повторно проверяет сохранённые manifest и отчёт A и пересчитывает хеши файлов. В журнале событие `rollback`.

Журнал публикаций после опыта: `bootstrap A → publish B → rollback A`.

**Сбой между указателем и журналом** (`--fail-after-pointer` на стенде, а в тесте 4 — внешняя инъекция `open-error publications.jsonl`):
1. Указатель уже показывает B, событий B 0, `exit 3`.
2. Повтор → `RECOVERED`: событие B дописано один раз с `recovered=true`.
3. Ещё повтор → `ALREADY_PUBLISHED`, событий B по-прежнему 1.

Реальный smoke своей модели выполнен отдельно от STUB-опытов (п. 7).

## 10. Ограничения

- Обслуживается baseline (закреплённая база), а не адаптер-кандидат: его веса недоступны на этой машине (п. 2). Загрузка через PEFT реализована, но без весов не проверялась.
- Gate для baseline — это сравнение «та же база, новый runtime (MPS/bf16) против reference ДЗ 6+7 (CUDA)» по `quality_gate` ДЗ 6+7. Все 53 ответа совпали, но выборка мала: 50 + 3.
- Замер: 8 + 8 запросов, короткие ответы (7 токенов), одна машина. Это не нагрузочное тестирование, p95, TTFT и ITL не измерялись. Backend последовательный.
- Один писатель журнала и указателя. Конкуренцию писателей и распределённые блокировки схема не решает.
- `accepted.json` — локальный указатель, а не alias MLflow Registry. Удалённый CI не запускался.
- Prefect работает во временном локальном сервере на каждый запуск (~2–3 с накладных расходов). Состояния задач видны в журналах и в `attempts.jsonl`. UI не поднимался.

## 11. Видео (5–8 минут)

Ссылка: _добавить после записи_. План:
1. `defects.csv` и `make red-green`: красный и зелёный логи (1 мин).
2. Сбой, восстановление и кеш на реальной модели: `evidence/model/01–03` и `release/attempts.jsonl` (1.5 мин).
3. Отказы: кандидат без весов, изменённые данные, неполный, чужой и NaN-отчёт; sha256 `accepted.json` до и после (1.5 мин).
4. `make check` зелёный и `scripts/break_demo.sh` с ненулевым кодом (1 мин).
5. Реальный API: 503 при загрузке, ответ модели, 422, затем A → B → A на стенде (1.5 мин).
6. Таблица замера и вывод про очередь (0.5 мин).

**Доступ к артефактам.** Веса не входят в Git: база — `Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca` с Hugging Face, её хеши записаны в manifest.
Отчёты оценки ДЗ 6+7 лежат в соседнем репозитории (`mlops26-hw6-7/hw67-broken/metrics`), их SHA-256 записаны в release-report.
Отчёты этой работы находятся в `release/`.
