"""Процесс выпуска через subprocess: отдельный рабочий каталог, настоящая файловая система."""
import json
import unittest

from tests_integration.harness import IntegrationCase, lab, read_events, sha256


class ReleaseProcessTests(IntegrationCase):

    def test_crash_recovery_uses_cache_and_input_change_invalidates(self):
        """Дефект 1: после сбоя подготовка берётся из кеша; новые байты/настройки — пересчёт."""
        ws = self.new_workspace()
        failed = lab(ws, 'run', '--fail-after-prepare')
        self.assertNotEqual(failed.returncode, 0, failed)
        self.assertIn('PREPARED', failed.stdout, failed)
        self.assertEqual(json.loads((ws / 'accepted.json').read_text())['model_id'], 'fixture-A')

        recovered = lab(ws, 'run')  # новый процесс, без сбоя
        self.assertEqual(recovered.returncode, 0, recovered)
        self.assertIn('CACHE_HIT', recovered.stdout, recovered)
        self.assertNotIn('PREPARED', recovered.stdout, 'подготовка не должна выполняться повторно')

        (ws / 'input.txt').write_text('Изменённый пример\n', encoding='utf-8')  # тот же путь
        new_sha = sha256(ws / 'input.txt')
        changed = lab(ws, 'run')
        self.assertEqual(changed.returncode, 0, changed)
        self.assertIn(f'PREPARED {new_sha}', changed.stdout,
                      f'изменённый вход по тому же пути обязан пересчитаться\n{changed}')
        self.assertNotIn('CACHE_HIT', changed.stdout)

        for key, value in [('max_length', 256), ('tokenizer_revision', 'r2'),
                           ('template_sha256', 'f' * 64), ('code_revision', 'prepare-v2')]:
            with self.subTest(setting=key):
                config = json.loads((ws / 'config.json').read_text())
                config[key] = value
                (ws / 'config.json').write_text(json.dumps(config))
                again = lab(ws, 'run')
                self.assertEqual(again.returncode, 0, again)
                self.assertIn('PREPARED', again.stdout, f'{key} не инвалидировал кеш\n{again}')

        attempts = [json.loads(l) for l in (ws / 'attempts.jsonl').read_text().splitlines()]
        states = [a['tasks'].get('prepare') for a in attempts]
        self.assertEqual(states[:3], ['Completed', 'Cached', 'Completed'], attempts)
        self.assertEqual(attempts[0]['outcome'], 'failed')

    def test_incomplete_foreign_and_rejected_reports_keep_pointer(self):
        """Дефект 2: неполный, чужой и отклонённый отчёт не меняют accepted.json ни на байт."""
        ws = self.new_workspace()
        before = (ws / 'accepted.json').read_bytes()
        for report in ['report-incomplete.json', 'report-A.json', 'report-reject.json']:
            with self.subTest(report=report):
                result = lab(ws, 'run', '--report', report)
                self.assertNotEqual(result.returncode, 0, f'{report} не должен давать выпуск\n{result}')
                self.assertNotIn('PUBLISHED', result.stdout)
                self.assertEqual((ws / 'accepted.json').read_bytes(), before)
        self.assertEqual(read_events(ws, 'fixture-B'), [])
        outcomes = [json.loads(l)['outcome'] for l in (ws / 'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(outcomes, ['rejected'] * 3)

    def test_disk_error_during_publish_keeps_previous_bytes(self):
        """Дефект 3: ошибка диска посреди записи указателя оставляет прежние байты."""
        ws = self.new_workspace()
        before = (ws / 'accepted.json').read_bytes()
        broken = lab(ws, 'run', fault=('partial-write', 'accepted.json'))
        self.assertNotEqual(broken.returncode, 0, broken)
        self.assertEqual((ws / 'accepted.json').read_bytes(), before,
                         'после ENOSPC accepted.json повреждён — запись не атомарна')
        self.assertEqual(sorted(p.name for p in ws.glob('*.tmp')), [])
        self.assertEqual(read_events(ws, 'fixture-B'), [])

        healthy = lab(ws, 'run')
        self.assertEqual(healthy.returncode, 0, healthy)
        self.assertEqual(json.loads((ws / 'accepted.json').read_text())['model_id'], 'fixture-B')
        self.assertEqual(len(read_events(ws, 'fixture-B')), 1)

    def test_repeated_release_publishes_once_and_recovers_after_pointer_crash(self):
        """Дефект 4: повтор выпуска в другом процессе не размножает события; сбой между
        указателем и журналом восстанавливается без двойной публикации."""
        ws = self.new_workspace('repeat')
        first = lab(ws, 'run')
        self.assertEqual(first.returncode, 0, first)
        pointer = (ws / 'accepted.json').read_bytes()
        second = lab(ws, 'run')
        self.assertEqual(second.returncode, 0, second)
        self.assertEqual(len(read_events(ws, 'fixture-B')), 1, 'повтор создал второе событие выпуска B')
        self.assertEqual((ws / 'accepted.json').read_bytes(), pointer)

        ws = self.new_workspace('crash')
        crashed = lab(ws, 'run', fault=('open-error', 'publications.jsonl'))
        self.assertNotEqual(crashed.returncode, 0, crashed)
        self.assertEqual(json.loads((ws / 'accepted.json').read_text())['model_id'], 'fixture-B')
        self.assertEqual(read_events(ws, 'fixture-B'), [])  # указатель есть, события нет
        for attempt in range(2):
            retry = lab(ws, 'run')
            self.assertEqual(retry.returncode, 0, retry)
            events = read_events(ws, 'fixture-B')
            self.assertEqual(len(events), 1, f'попытка {attempt}: {events}')
        self.assertTrue(events[0]['recovered'])
        attempts = [json.loads(l)['outcome'] for l in (ws / 'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(attempts, ['failed', 'recovered', 'already_published'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
