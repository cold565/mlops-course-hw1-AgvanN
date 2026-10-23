# Разбор четырёх дефектов части A

Формат: симптом → причина → исправление → доказательство (номер проверки `make check`).

## 1. Все кандидаты пишут в один общий run

**Симптом.** В эксперименте `hw6-lora` под родительским `sweep` появлялся один
дочерний run `all-candidates`. Параметры `r`, `lr`, `target_modules` в нём —
только от первого кандидата, а кривые `val_loss` шести моделей наложены на одни
и те же шаги 0…4. Метрики `final_val_loss` перезаписывались, артефакт `model`
оставался от последнего кандидата. Сравнивать кандидатов и выбрать лучший
по такому run невозможно.

**Причина.** `src/sweep.py` открывал **один** вложенный run и в цикле вызывал
`run_candidate` внутри него. Маскировка: `if not ...data.params:` пропускала
`log_params` для 2–6 кандидатов, чтобы MLflow не падал на повторной записи
параметра с другим значением.

**Исправление.**

```python
# было
with mlflow.start_run(run_name="all-candidates", nested=True):
    for candidate in p["sweep"]:
        rows.append(run_candidate(p, candidate, versions))
# стало
for candidate in p["sweep"]:
    with mlflow.start_run(run_name=candidate["name"], nested=True):
        rows.append(run_candidate(p, candidate, versions))
```

Условие вокруг `log_params` удалено: каждый run пишет свои параметры.

**Доказательство.** Проверка 1 (шесть отдельных законченных runs с разными
`tags.candidate`), проверка 3 (параметры и кривые каждого run совпадают с его
конфигурацией), проверка 4 (реальный `r` и `target_modules` в
`adapter_config.json` совпадают с логом run).

## 2. Артефакты пропадают после пересоздания контейнера MLflow

**Симптом.** После `docker compose up --force-recreate mlflow` метаданные runs
на месте (они в Postgres), но скачать `model/`, `samples.json` или
`persistence/probe.txt` нельзя: файлы не найдены.

**Причина.** Сервер пишет файлы в `--artifacts-destination=/mlartifacts`, но
каталог не смонтирован ни в какой volume — это слой самого контейнера.
Volume `artifacts` объявлен в `compose.yaml`, но не подключён к сервису.
`docker compose restart` такую ошибку не показывает: контейнер тот же.

**Исправление.** `compose.yaml`, сервис `mlflow`:

```yaml
volumes:
  - artifacts:/mlartifacts
```

**Доказательство.** `make check-storage`: записать probe-файл со случайным
содержимым → запомнить SHA256 → `up --force-recreate mlflow` → скачать и
сверить SHA256 (`metrics/storage-check.json`). Проверка 8 повторно скачивает
файл и сверяет хеш и неизменность `compose.yaml`.

## 3. Нет версий train/val в runs

**Симптом.** У runs есть `git_sha`, `code_sha256`, `config_sha256`, но нет
`data_train_sha256` / `data_val_sha256`. Если подменить `data/tokenized/*.pt`
при том же коде, runs выглядят одинаково, а `val_loss` сравнивается на разных
данных. `src.promote.select` при этом всегда падал: требует эти теги.

**Причина.** В `src/sweep.py` версии данных выбрасывались из provenance:
`{k: v for k, v in provenance(p).items() if not k.startswith("data_")}`.

**Исправление.** `versions = provenance(p)` целиком: родительский и каждый
дочерний run получают SHA256 файлов train и val. Сами файлы версионируются
DVC (`data/tokenized.dvc`), их хеши совпадают с тегами.

**Доказательство.** Проверка 2 сравнивает теги каждого run с текущими
SHA256 файлов, кода и конфига; `make candidate` отказывает, если хотя бы
один run устарел.

## 4. Регистрация обходит порог по loss

**Симптом.** `src.promote.promote` регистрировал любую модель и ставил ей тег
`validation=passed`, даже если `final_val_loss / base_val_loss` выше порога.
Проверка «нулевой порог должен дать отказ» проходила с созданием версии.

**Причина.** Функция `gate()` была написана, но в `promote()` не вызывалась:
ratio считался только для тега (`# TODO: достаточно ли этого?`).

**Исправление.**

```python
ratio = gate(run, threshold)          # ValueError при непройденном пороге / NaN / статусе
version = mlflow.register_model(...)  # только после проверки
```

Порог проверяется **до** `register_model`, поэтому отказ не оставляет версию
в реестре и не трогает alias.

**Доказательство.** Проверка 6: `promote(..., threshold=0.0)` с положительным
loss обязан вызвать `ValueError`. `make candidate` использует тот же `promote`
с порогом `max_val_ratio = 1.0` из `params.yaml`.
