"""Где лежат файлы артефакта, описанного в manifest. Веса никогда не передаются между задачами."""
from pathlib import Path


def artifact_root(manifest: dict, workspace: Path) -> Path:
    """hf://name@revision -> снимок в локальном кеше HF; file://path -> каталог; иначе workspace."""
    uri = manifest['artifact_uri']
    if uri.startswith('hf://'):
        from huggingface_hub import snapshot_download
        name, _, revision = uri[len('hf://'):].partition('@')
        if len(revision) != 40:
            raise ValueError('Для базы нужна точная commit-ревизия (40 hex)')
        return Path(snapshot_download(name, revision=revision, local_files_only=True))
    if uri.startswith('file://'):
        path = Path(uri[len('file://'):])
        return path if path.is_absolute() else (Path(workspace) / path)
    return Path(workspace)
