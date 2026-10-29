"""Реальный локальный HTTP-сервер (отдельный процесс) со STUB-моделью."""
import json
import time
import unittest

from tests_integration.harness import IntegrationCase, Service, answer_text, lab


class ServiceHttpTests(IntegrationCase):

    def test_not_ready_until_warmup_finished(self):
        """Дефект 5: health жив сразу, но ready и generate не обслуживают до конца прогрева."""
        ws = self.new_workspace()
        with Service(ws, warmup=6) as svc:
            self.assertEqual(svc.get('/health')[0], 200)
            status, body = svc.get('/debug')
            self.assertFalse(body['warmup_finished'], 'тест должен начаться до конца прогрева')
            self.assertEqual(svc.get('/ready')[0], 503, 'ready до окончания прогрева')
            status, body = svc.post('/generate', {'prompt': 'Which planet has rings?', 'max_new_tokens': 8})
            self.assertEqual(status, 503, f'generate до прогрева: {status} {body}')
            self.assertEqual(svc.get('/debug')[1]['calls'], 0)

            ready = svc.wait_ready(timeout=30)
            self.assertTrue(svc.get('/debug')[1]['warmup_finished'])
            self.assertEqual(ready['model_id'], 'fixture-A')
            status, body = svc.post('/generate', {'prompt': 'Which planet has rings?', 'max_new_tokens': 8})
            self.assertEqual(status, 200, body)
            self.assertEqual(svc.get('/debug')[1]['calls'], 1)

    def test_loaded_version_changes_only_after_restart_and_rollback(self):
        """Дефект 6: сервис сообщает загруженную версию, а не текущий файл указателя.
        Опыт A -> публикация B -> перезапуск B -> откат A -> перезапуск A."""
        ws = self.new_workspace()

        def ask(svc):
            status, body = svc.post('/generate', {'prompt': 'version?', 'max_new_tokens': 16})
            self.assertEqual(status, 200, body)
            return body

        svc = Service(ws, warmup=0).start()
        try:
            svc.wait_ready()
            body = ask(svc)
            self.assertEqual(body['model_id'], 'fixture-A')
            published = lab(ws, 'run')  # другой процесс публикует B
            self.assertEqual(published.returncode, 0, published)
            self.assertEqual(json.loads((ws / 'accepted.json').read_text())['model_id'], 'fixture-B')
            body = ask(svc)
            self.assertEqual(body['model_id'], 'fixture-A', 'до перезапуска в памяти A, а API сообщает B')
            self.assertIn('fixture-A', answer_text(body))
            self.assertEqual(svc.get('/debug')[1]['loaded_id'], 'fixture-A')
        finally:
            svc.stop()

        with Service(ws, warmup=0) as svc:
            svc.wait_ready()
            body = ask(svc)
            self.assertEqual(body['model_id'], 'fixture-B')
            self.assertIn('version B', answer_text(body))

        rollback = lab(ws, 'rollback')
        self.assertEqual(rollback.returncode, 0, rollback)
        self.assertEqual(json.loads((ws / 'accepted.json').read_text())['model_id'], 'fixture-A')
        with Service(ws, warmup=0) as svc:
            ready = svc.wait_ready()
            body = ask(svc)
            self.assertEqual(body['model_id'], 'fixture-A')
            self.assertIn('version A', answer_text(body))
            self.assertEqual(body['release_id'], ready['release_id'])

    def test_invalid_requests_rejected_without_backend_call(self):
        """Дефект 7: неверный запрос получает 4xx до генерации; число вызовов backend не меняется."""
        ws = self.new_workspace()
        with Service(ws, warmup=0) as svc:
            svc.wait_ready()
            calls = svc.get('/debug')[1]['calls']
            bad = [{'prompt': ''}, {'prompt': '   '}, {'max_new_tokens': 4},
                   {'prompt': 'x', 'max_new_tokens': 0}, {'prompt': 'x', 'max_new_tokens': 33},
                   {'prompt': 'x', 'max_new_tokens': 100000}, {'prompt': 'x', 'max_new_tokens': 'many'},
                   {'prompt': 'x', 'max_new_tokens': 1.5}, {'prompt': 123}]
            for body in bad:
                with self.subTest(body=body):
                    status, payload = svc.post('/generate', body)
                    self.assertTrue(400 <= status < 500, f'{body} -> {status} {payload}')
            status, _ = svc.post('/generate', raw=b'{not json')
            self.assertTrue(400 <= status < 500, status)
            self.assertEqual(svc.get('/debug')[1]['calls'], calls, 'неверный запрос вызвал backend')

            started = time.perf_counter()
            status, body = svc.post('/generate', {'prompt': 'Which planet has rings?', 'max_new_tokens': 32})
            self.assertEqual(status, 200, body)
            self.assertLess(time.perf_counter() - started, 10)
            self.assertEqual(svc.get('/debug')[1]['calls'], calls + 1)
            for key in ['request_id', 'model_id', 'runtime_sha256', 'output_tokens', 'generation_seconds']:
                self.assertIn(key, body)
            self.assertLessEqual(body['output_tokens'], 32)


if __name__ == '__main__':
    unittest.main(verbosity=2)
