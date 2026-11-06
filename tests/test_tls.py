"""HTTPS interception: Dopamine CA, decrypted proxy traffic, pass-through, rejections and the certificate page."""
import http.client
import json
import socket
import ssl
import tempfile
import threading
import time
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from server import Engine, LocalServer, APIHandler
from test_engine import Fixture
from tls_ca import normalize_host


class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.engine = Engine(cls.temp.name)
        # The upstream test server presents a certificate from the same CA, so verification can be switched on and off.
        cls.fixture = LocalServer(('127.0.0.1', 0), Fixture)
        cls.fixture.socket = cls.engine.ca.context_for('localhost').wrap_socket(cls.fixture.socket, server_side=True)
        cls.api = LocalServer(('127.0.0.1', 0), APIHandler)
        cls.api.engine = cls.engine
        for server in (cls.fixture, cls.api):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        cls.engine.set_proxy(True, port)
        cls.upstream = cls.fixture.server_port

    @classmethod
    def tearDownClass(cls):
        cls.engine.set_proxy(False)
        for server in (cls.fixture, cls.api):
            server.shutdown(); server.server_close()
        cls.engine.db.close(); cls.temp.cleanup()

    def setUp(self):
        self.engine.configure_tls({'intercept': True, 'verify_upstream': False, 'passthrough': []})
        self.engine.intercept = False

    def client_context(self):
        context = ssl.create_default_context(cafile=str(self.engine.ca.ca_cert))
        return context

    def connect(self, host='localhost', context=None):
        conn = http.client.HTTPSConnection('127.0.0.1', self.engine.proxy_port, timeout=6, context=context or self.client_context())
        conn.set_tunnel(host, self.upstream)
        return conn

    def get(self, path, host='localhost'):
        conn = self.connect(host)
        conn.request('GET', path)
        r = conn.getresponse(); result = (r.status, r.read()); conn.close()
        return result

    def latest(self):
        return self.engine.history(self.engine.history()[0]['id'])

    def api_call(self, path, data=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.api.server_port, timeout=10)
        conn.request('POST' if data is not None else 'GET', path, json.dumps(data) if data is not None else None, {'Content-Type': 'application/json', 'X-Dopamine-Token': self.engine.api_token})
        r = conn.getresponse(); body = r.read(); conn.close()
        return r.status, (json.loads(body) if r.getheader('Content-Type', '').startswith('application/json') else body)

    def test_decrypted_request_is_recorded(self):
        self.assertEqual(self.get('/secure?x=1'), (200, b'fixture:/secure?x=1'))
        item = self.latest()
        self.assertEqual(item['url'], f'https://localhost:{self.upstream}/secure?x=1')
        self.assertFalse(item['tunnel'])
        self.assertTrue(item['request'].startswith('GET /secure?x=1 HTTP/1.1'))
        self.assertIn('fixture:/secure', item['response'])

    def test_ip_address_certificate(self):
        self.assertEqual(self.get('/ip', host='127.0.0.1')[0], 200)

    def test_keep_alive_reuses_decrypted_connection(self):
        conn = self.connect()
        for path in ('/one', '/two'):
            conn.request('GET', path)
            r = conn.getresponse()
            self.assertEqual(r.read(), b'fixture:' + path.encode())
            self.assertEqual(r.getheader('Connection'), 'keep-alive')
        conn.close()

    def test_post_body(self):
        conn = self.connect()
        conn.request('POST', '/submit', b'{"a":1}', {'Content-Type': 'application/json'})
        r = conn.getresponse(); self.assertEqual((r.status, r.read()), (201, b'{"a":1}')); conn.close()

    def test_intercept_and_edit_https_request(self):
        self.engine.intercept = True
        results = []
        thread = threading.Thread(target=lambda: results.append(self.get('/before')))
        thread.start()
        for _ in range(200):
            items = self.api_call('/api/intercept')[1]['items']
            if items:
                break
            time.sleep(.01)
        self.assertTrue(items[0]['url'].startswith('https://localhost:'))
        self.engine.resolve(items[0]['id'], 'forward', request=items[0]['request'].replace('/before', '/after'))
        thread.join(6)
        self.assertEqual(results[0], (200, b'fixture:/after'))

    def test_upstream_verification(self):
        self.engine.configure_tls({'verify_upstream': True})
        status, _ = self.get('/verified')
        self.assertEqual(status, 502)
        self.assertIn('CERTIFICATE_VERIFY_FAILED', self.latest()['error'])

    def test_passthrough_host_is_tunneled(self):
        self.engine.configure_tls({'passthrough': ['*.example', 'localhost']})
        self.assertEqual(self.get('/tunneled'), (200, b'fixture:/tunneled'))
        item = self.latest()
        self.assertTrue(item['tunnel'])
        self.assertEqual(item['method'], 'CONNECT')

    def test_interception_off_tunnels(self):
        self.engine.configure_tls({'intercept': False})
        self.get('/off')
        self.assertTrue(self.latest()['tunnel'])

    def test_rejected_certificate_is_reported(self):
        with self.assertRaises(ssl.SSLError):
            self.get_with(ssl.create_default_context())
        for _ in range(100):
            if self.engine.tls_rejection:
                break
            time.sleep(.01)
        self.assertEqual(self.engine.tls_rejection['host'], 'localhost')
        self.assertIn('rejected the Dopamine certificate', self.latest()['error'])

    def get_with(self, context):
        conn = self.connect(context=context)
        try:
            conn.request('GET', '/')
            conn.getresponse().read()
        finally:
            conn.close()

    def test_certificate_page_through_proxy(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.engine.proxy_port, timeout=5)
        conn.request('GET', 'http://dopamine/cert.pem')
        r = conn.getresponse(); body = r.read(); conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(body, self.engine.ca.ca_cert.read_bytes())
        conn = http.client.HTTPConnection('127.0.0.1', self.engine.proxy_port, timeout=5)
        conn.request('GET', 'http://dopamine/')
        r = conn.getresponse(); page = r.read().decode(); conn.close()
        self.assertIn('Dopamine certificate', page)
        self.assertIn(self.engine.ca.info()['sha256'], page)

    def test_api_settings_and_validation(self):
        status, info = self.api_call('/api/tls')
        self.assertEqual(status, 200)
        self.assertTrue(info['ca']['exists'])
        self.assertIn('trusted', info['ca'])
        self.assertEqual(len(info['ca']['sha256'].split(':')), 32)
        status, info = self.api_call('/api/tls', {'passthrough': [' *.Apple.com ', '*.apple.com', '']})
        self.assertEqual(info['passthrough'], ['*.apple.com'])
        self.assertEqual(self.api_call('/api/tls', {'passthrough': ['bad host/x']})[0], 400)
        self.assertEqual(self.api_call('/api/tls', {'intercept': 'yes'})[0], 400)
        status, pem = self.api_call('/api/tls/ca.pem')
        self.assertTrue(pem.startswith(b'-----BEGIN CERTIFICATE-----'))
        self.assertTrue(Engine(self.temp.name).settings['passthrough'] == ['*.apple.com'])

    def test_zz_regenerate_replaces_ca(self):
        before = self.engine.ca.info()['sha256']
        status, info = self.api_call('/api/tls/regenerate', {})
        self.assertEqual(status, 200)
        self.assertNotEqual(info['ca']['sha256'], before)
        # Upstream fixture still uses the old CA; verification is off, and the client trusts the new CA.
        self.assertEqual(self.get('/after-regenerate')[0], 200)

    def test_host_validation(self):
        self.assertEqual(normalize_host('[::1]'), '::1')
        self.assertEqual(normalize_host('Example.COM.'), 'example.com')
        for bad in ('a b', 'a/b', '', '-x' * 200, 'x\ny'):
            with self.assertRaises(ValueError):
                normalize_host(bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
