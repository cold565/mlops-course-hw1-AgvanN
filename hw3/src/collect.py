"""Стадия collect: источник → data/raw.jsonl."""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from src.config import load_params

def pick_prompt(example_id: str, variants: list[str]) -> str:
    digest = hashlib.sha1(example_id.encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]

def main() -> None:
    params = load_params()
    cfg = params["collect"]
    paths = params["paths"]
    n_rows = cfg["n_rows"]
    variants = cfg["system_prompts"]
    if not variants:
        raise SystemExit("collect.system_prompts пуст")

    # Для v2 берем данные с конца файла и увеличиваем количество
    is_v2 = (cfg["version"] == "v2")
    if is_v2:
        n_rows += 500

    allowed_topics = {"brainstorming", "general_qa", "information_extraction", "open_qa", "creative_writing"}
    out = Path(paths["raw"])
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    scanned = written = dropped_topic = 0
    prompts_used: set[str] = set()

    url = "https://huggingface.co/datasets/databricks/databricks-dolly-15k/resolve/main/databricks-dolly-15k.jsonl"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    with urllib.request.urlopen(req) as response:
        lines = response.read().decode('utf-8').splitlines()

    # Смена контента для честной разницы v1 и v2
    if is_v2:
        lines = lines[::-1]

    with out.open("w", encoding="utf-8") as fh:
        for line in lines:
            if written >= n_rows:
                break
            
            scanned += 1
            item = json.loads(line)
            topic = item.get("category", "general")
            
            if topic not in allowed_topics:
                dropped_topic += 1
                continue

            raw_text = item.get("instruction", "") + item.get("response", "")
            ex_id = hashlib.md5(raw_text.encode("utf-8")).hexdigest()

            prompt = pick_prompt(ex_id, variants)
            prompts_used.add(prompt)

            user_content = item.get("instruction", "")
            context = item.get("context", "")
            if context:
                user_content += f"\n\nКонтекст: {context}"

            record = {
                "id": ex_id,
                "topic": topic,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content.strip()},
                    {"role": "assistant", "content": item.get("response", "")}
                ]
            }
            
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    metrics = {
        "version": cfg["version"],
        "rows_scanned": scanned,
        "rows_written": written,
        "dropped_topic_filter": dropped_topic,
        "topics_filter": len(allowed_topics),
        "system_prompt_variants": len(prompts_used),
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_collect"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()