"""验证宿主服务入口、交易流程与请求边界。"""
import importlib
from io import BytesIO
import json
from pathlib import Path
from socketserver import ThreadingMixIn
import threading
import unittest
import uuid
from urllib.request import Request, urlopen
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator
import app_wsgi
import database
import tests


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class HostedTests(tests.TrailKitTests):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent / 'test_runs'
        self.temp = self.test_root / str(uuid.uuid4())
        self.temp.mkdir(parents=True)
        database.DB_PATH = self.temp / 'test.db'
        database.initialize(demo=False)
        self.server = make_server('127.0.0.1', 0, validator(app_wsgi.application),
                                  server_class=ThreadedServer, handler_class=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.customer, self.sales, self.admin = [tests.Client(self.base) for _ in range(3)]

    def test_static_head_and_database_not_exposed(self):
        with urlopen(self.base + '/') as r:
            self.assertIn('山野装备', r.read().decode())
            self.assertIn('text/html', r.headers['Content-Type'])
            self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])
        with urlopen(Request(self.base + '/app.js', method='HEAD')) as r:
            self.assertGreater(int(r.headers['Content-Length']), 0)
            self.assertEqual(r.read(), b'')
        for path in ('/data/trailkit.db', '/server.py', '/static/../database.py'):
            with self.assertRaises(tests.HTTPError) as err:
                urlopen(self.base + path)
            self.assertEqual(err.exception.code, 404)

    def test_https_cookie_and_unsupported_method(self):
        env = {}
        setup_testing_defaults(env)
        body = json.dumps({'username':'nikita','password':'TrailKit2026!'}).encode()
        env.update(REQUEST_METHOD='POST', PATH_INFO='/api/login', QUERY_STRING='',
                   CONTENT_TYPE='application/json', CONTENT_LENGTH=str(len(body)))
        env['wsgi.url_scheme'] = 'https'
        env['wsgi.input'] = BytesIO(body)
        received = []
        result = validator(app_wsgi.application)(env, lambda s,h: received.append((s,h)))
        try:
            self.assertTrue(json.loads(b''.join(result))['ok'])
        finally:
            result.close()
        cookie = dict(received[0][1])['Set-Cookie']
        self.assertIn('; Secure', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        with self.assertRaises(tests.HTTPError) as err:
            urlopen(Request(self.base + '/api/login', method='DELETE'))
        self.assertEqual(err.exception.code, 405)

    def test_reload_preserves_orders_and_chinese_search(self):
        self.customer.login()
        oid = self.order()
        self.assertEqual(self.customer.call('pay', {'id':oid})[0], 200)
        importlib.reload(app_wsgi)
        records = self.customer.call('orders')[1]
        self.assertEqual(records[0]['id'], oid)
        self.assertEqual(records[0]['status'], 'paid')
        self.assertEqual(len(self.customer.call('products?q=%E5%B8%90%E7%AF%B7')[1]), 1)
        with database.connect() as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')

    def test_initial_password_is_applied_to_all_roles(self):
        database.DB_PATH = self.temp / 'host.db'
        password = 'HostSetupForTestOnly2026!'
        database.initialize(demo=False, initial_password=password)
        for name in ('nikita','sales','admin'):
            self.assertEqual(tests.Client(self.base).login(name, password), 200)
            self.assertEqual(tests.Client(self.base).login(name, 'TrailKit2026!'), 401)


if __name__ == '__main__':
    unittest.main(verbosity=2)
