"""Вход стадии train — выход стадии tokenize из ДЗ 4.

Формат файла: torch.save словаря {"examples": [...], "pad_token_id", ...},
где у каждого примера input_ids / attention_mask / labels — списки int,
а labels = -100 на промпте. Здесь ничего не токенизируется заново:
маску и шаблон уже проверили в ДЗ 4.
"""

import random
from pathlib import Path

import torch

LABEL_PAD_ID = -100


def load_split(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Нет {p}.\n"
            "Вход стадии train — выход стадии tokenize из ДЗ 4 (data/tokenized/*.pt).\n"
            "Укажите пути к своим файлам в params.yaml, секция data."
        )
    blob = torch.load(p, weights_only=False)
    if not blob.get("examples"):
        raise SystemExit(f"{p}: в файле нет примеров")
        
    # --- НАШ ХАК ДЛЯ ВИДЕОКАРТЫ ---
    valid_examples = []
    for ex in blob["examples"]:
        # Режем до 512 токенов
        ex["input_ids"] = ex["input_ids"][:512]
        ex["attention_mask"] = ex["attention_mask"][:512]
        ex["labels"] = ex["labels"][:512]
        
        # Если после обрезки остался хоть один токен ответа (не -100) — оставляем пример
        if any(x != LABEL_PAD_ID for x in ex["labels"]):
            valid_examples.append(ex)
            
    blob["examples"] = valid_examples
    # ------------------------------

    return blob


def pad_batch(features: list[dict], pad_id: int) -> dict[str, torch.Tensor]:
    """Динамический паддинг слева, labels на паддинге -100 — как в ДЗ 4."""
    width = max(len(f["input_ids"]) for f in features)

    def left(seq, value):
        return [value] * (width - len(seq)) + list(seq)

    return {
        "input_ids": torch.tensor([left(f["input_ids"], pad_id) for f in features]),
        "attention_mask": torch.tensor([left(f["attention_mask"], 0) for f in features]),
        "labels": torch.tensor([left(f["labels"], LABEL_PAD_ID) for f in features]),
    }


def batches(examples: list[dict], batch_size: int, pad_id: int, shuffle: bool, seed: int):
    order = list(range(len(examples)))
    if shuffle:
        random.Random(seed).shuffle(order)
    for i in range(0, len(order), batch_size):
        yield pad_batch([examples[j] for j in order[i:i + batch_size]], pad_id)
