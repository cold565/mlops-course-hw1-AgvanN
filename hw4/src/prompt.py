"""Сборка текста диалога.

Модуль общий для обучения и инференса.
"""

from typing import Any


def _template_kwargs(params: dict) -> dict:
    """Доп. аргументы шаблона, которые есть не у всех моделей.

    `enable_thinking` понимает только семейство с режимом рассуждений (Qwen3);
    остальные шаблоны молча его игнорируют, поэтому передаём только если задан.
    """
    value = params["model"].get("enable_thinking")
    return {} if value is None else {"enable_thinking": value}


def split_messages(messages: list[dict]) -> tuple[list[dict], dict]:
    """Разделить диалог на промпт и ответ ассистента.

    Промпт — всё, что модель видит на входе. Ответ — последняя реплика
    ассистента, ровно она и должна попасть в лосс.
    """
    if not messages or messages[-1]["role"] != "assistant":
        raise ValueError("последняя реплика диалога обязана быть ответом ассистента")
    return messages[:-1], messages[-1]


def build_chat_text(
    tokenizer: Any,
    messages: list[dict],
    params: dict,
    add_generation_prompt: bool,
) -> str:
    is_training_example = (messages and messages[-1]["role"] == "assistant")
    
    if is_training_example:
        prompt_msgs, ans_msg = split_messages(messages)
    else:
        prompt_msgs = messages
        
    # Всегда собираем промпт через add_generation_prompt=True, чтобы префиксы совпадали
    prompt = tokenizer.apply_chat_template(
        prompt_msgs,
        tokenize=False,
        add_generation_prompt=True,
        **_template_kwargs(params),
    )
    
    # Если это инференс, просто отдаем промпт
    if add_generation_prompt or not is_training_example:
        return prompt
        
    # Для обучения вручную доклеиваем ответ и eos токен
    return prompt + ans_msg["content"] + tokenizer.eos_token


def prompt_token_len(
    tokenizer: Any,
    prompt_text: str,
    full_ids: list[int],
    full_offsets: list[tuple[int, int]],
) -> tuple[int, bool]:
    """Длина промпта в токенах — граница маскирования лосса.

    Возвращает `(число токенов промпта, сработал ли запасной путь)`.

    Основной путь: токенизировать промпт отдельно и убедиться, что его токены
    совпадают с началом токенов полного текста. Обычно так и есть.

    Запасной путь нужен из-за BPE: токенизатор режет не по символам, а по
    статистике, и токен может «склеить» конец промпта с началом ответа —
    тогда пары одинаковых токенов на границе нет. Тогда берём границу по
    символам: первый токен, который НАЧИНАЕТСЯ не раньше конца промпта.
    Склеенный токен при этом уходит в промпт (маскируется). Это осознанный
    выбор: потерять первый символ ответа из лосса безопаснее, чем учить
    модель дописывать хвост промпта. Доля таких примеров пишется в метрики.
    """
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    n = len(prompt_ids)
    if full_ids[:n] == prompt_ids:
        return n, False

    boundary = len(prompt_text)
    for i, (start, _end) in enumerate(full_offsets):
        if start >= boundary:
            return i, True
    raise ValueError(
        "не нашли начало ответа ассистента: за границей промпта не осталось "
        "ни одного токена (текст оборван либо ответ целиком склеился с промптом)"
    )