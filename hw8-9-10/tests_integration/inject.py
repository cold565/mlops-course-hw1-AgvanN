"""Внешняя инъекция сбоя ввода-вывода (mock) для процесса под тестом. Диск не заполняется.

  python inject.py partial-write accepted.json -- lab.py --workspace W run
      запись в файл, имя которого начинается с accepted.json (включая временный файл рядом),
      записывает половину байтов и падает с ENOSPC — как ошибка диска посреди записи.
  python inject.py open-error publications.jsonl -- lab.py ...
      открытие такого файла на запись/дозапись падает: сбой после обновления указателя,
      но до записи журнала.
Код под тестом об инъекции не знает: подменяются io.open/builtins.open.
"""
import builtins
import errno
import io
import os
import runpy
import sys

FAULT, PATTERN = sys.argv[1], sys.argv[2]
SEP = sys.argv.index('--')
SCRIPT, ARGS = sys.argv[SEP + 1], sys.argv[SEP + 2:]
_real_open = io.open


class PartialWriteFile:
    def __init__(self, f):
        self._f = f

    def write(self, data):
        self._f.write(data[:len(data) // 2])
        self._f.flush()
        raise OSError(errno.ENOSPC, 'Injected: No space left on device')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()
        return False

    def __getattr__(self, name):
        return getattr(self._f, name)


def faulty_open(file, mode='r', *args, **kwargs):
    name = '' if isinstance(file, int) else os.path.basename(os.fspath(file))
    writing = any(flag in mode for flag in 'wxa+')
    if writing and name.startswith(PATTERN):
        if FAULT == 'open-error':
            raise OSError(errno.EIO, f'Injected: cannot open {name}')
        if FAULT == 'partial-write':
            return PartialWriteFile(_real_open(file, mode, *args, **kwargs))
    return _real_open(file, mode, *args, **kwargs)


io.open = faulty_open
builtins.open = faulty_open
sys.argv = [SCRIPT, *ARGS]
sys.path.insert(0, os.path.dirname(os.path.abspath(SCRIPT)))
runpy.run_path(SCRIPT, run_name='__main__')
