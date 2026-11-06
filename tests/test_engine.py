import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from server import Engine, LocalServer, APIHandler

class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ('fixture:' + self.path).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Duplicate', 'one')
        self.send_header('X-Duplicate', 'two')
        self.end_headers()
        self.wfile.write(body)
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Length', '123')
        self.end_headers()
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(201)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_): pass

class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.engine = Engine(cls.temp.name)
        cls.fixture = LocalServer(('127.0.0.1', 0), Fixture)
        cls.api = LocalServer(('127.0.0.1', 0), APIHandler)
        cls.api.engine = cls.engine
        for server in (cls.fixture, cls.api):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        sock = socket.socket(); sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]; sock.close()
        cls.engine.set_proxy(True,port)
        cls.target = f'http://127.0.0.1:{cls.fixture.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.engine.set_proxy(False)
        for server in (cls.fixture,cls.api): server.shutdown(); server.server_close()
        cls.engine.db.close(); cls.temp.cleanup()
    def api_call(self,path,data=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.api.server_port,timeout=5)
        h={'Content-Type':'application/json','X-Dopamine-Token':self.engine.api_token}; h.update(headers or {})
        h={k:v for k,v in h.items() if v is not None}
        conn.request('POST' if data is not None else 'GET',path,json.dumps(data) if data is not None else None,h)
        r=conn.getresponse(); result=(r.status,json.loads(r.read()));conn.close();return result
    def proxy(self,path='/',body=None,chunked=False):
        conn=http.client.HTTPConnection('127.0.0.1',self.engine.proxy_port,timeout=6)
        conn.request('POST' if body is not None else 'GET',self.target+path,body,encode_chunked=chunked)
        r=conn.getresponse(); result=(r.status,r.read(),r.getheaders());conn.close();return result
    def pending(self):
        for _ in range(100):
            items=self.api_call('/api/intercept')[1]['items']
            if items:return items[0]
            time.sleep(.01)
        self.fail('No pending request')
    def tearDown(self):
        self.api_call('/api/intercept', {'enabled': False})

    def begin_held(self, path='/held'):
        self.api_call('/api/intercept', {'enabled': True})
        results = []
        thread = threading.Thread(target=lambda: results.append(self.proxy(path)))
        thread.start()
        return self.pending(), thread, results

    def test_response_interception_edit_and_retry(self):
        pending, thread, results = self.begin_held('/response')
        self.assertEqual(pending['stage'], 'request')
        self.api_call('/api/intercept/' + pending['id'] + '/forward', {'intercept_response': True})
        held = self.pending()
        self.assertEqual(held['stage'], 'response')
        self.assertFalse(results)
        endpoint = '/api/intercept/' + held['id'] + '/forward'
        self.assertEqual(self.api_call(endpoint, {'response': 'invalid'})[0], 400)
        self.assertFalse(results)
        edited = 'HTTP/1.1 202 Accepted\r\nX-Edited: yes\r\nContent-Length: 999\r\n\r\nedited body'
        self.assertEqual(self.api_call(endpoint, {'response': edited})[0], 200)
        self.assertEqual(self.api_call(endpoint, {})[0], 404)
        thread.join(5)
        self.assertEqual(results[0][0:2], (202, b'edited body'))
        self.assertIn(('X-Edited', 'yes'), results[0][2])
        self.assertIn(('Content-Length', '11'), results[0][2])
        saved = self.engine.history(self.engine.history()[0]['id'])
        self.assertEqual(saved['status'], 202)
        self.assertTrue(saved['response'].endswith('edited body'))

    def test_response_drop_and_repeater_bypass(self):
        pending, thread, results = self.begin_held('/secret')
        self.api_call('/api/intercept/' + pending['id'] + '/forward', {'intercept_response': True})
        held = self.pending()
        _, repeated = self.api_call('/api/repeater', {'url': self.target, 'request': 'GET /direct HTTP/1.1\n\n'})
        self.assertEqual(repeated['status'], 200)
        self.api_call('/api/intercept/' + held['id'] + '/drop', {})
        thread.join(5)
        self.assertEqual(results[0][0], 502)
        self.assertNotIn(b'fixture:/secret', results[0][1])
        self.assertIn('Response dropped', self.engine.history()[0]['error'])

    def test_target_edit_and_invalid_target_retry(self):
        held, thread, results = self.begin_held('/old')
        endpoint = '/api/intercept/' + held['id'] + '/forward'
        self.assertEqual(self.api_call(endpoint, {'url': 'file:///tmp/a'})[0], 400)
        self.assertEqual(self.api_call(endpoint, {'url': self.target, 'request': 'GET /new HTTP/1.1\r\n\r\n'})[0], 200)
        thread.join(5)
        self.assertEqual(results[0][1], b'fixture:/new')

    def test_bulk_edits_validate_before_releasing(self):
        first, t1, r1 = self.begin_held('/first')
        r2 = []
        t2 = threading.Thread(target=lambda: r2.append(self.proxy('/second')))
        t2.start()
        for _ in range(100):
            held = self.api_call('/api/intercept')[1]['items']
            if len(held) == 2: break
            time.sleep(.01)
        self.assertEqual(len(held), 2)
        edits = {p['id']: {'request': p['request'].replace('/first', '/edited')} for p in held}
        edits[held[1]['id']]['request'] = 'bad'
        self.assertEqual(self.api_call('/api/intercept/bulk', {'action': 'forward', 'edits': edits})[0], 400)
        self.assertFalse(r1 or r2)
        self.assertEqual(len(self.api_call('/api/intercept')[1]['items']), 2)
        edits[held[1]['id']]['request'] = held[1]['request']
        status, result = self.api_call('/api/intercept/bulk', {'action': 'forward', 'edits': edits})
        self.assertEqual((status, result['count']), (200, 2))
        t1.join(5); t2.join(5)
        self.assertEqual(r1[0][1], b'fixture:/edited')
        self.assertEqual(r2[0][1], b'fixture:/second')

    def test_binary_response_intercept_without_edits(self):
        self.api_call('/api/intercept', {'enabled': True})
        binary = bytes(range(256))
        results = []
        thread = threading.Thread(target=lambda: results.append(self.proxy('/binary-held', binary)))
        thread.start()
        held = self.pending()
        self.api_call('/api/intercept/' + held['id'] + '/forward', {'intercept_response': True})
        held = self.pending()
        self.assertEqual(held['stage'], 'response')
        self.api_call('/api/intercept/' + held['id'] + '/forward', {'response': held['response']})
        thread.join(5)
        self.assertEqual(results[0][1], binary)

    def test_bulk_drop_and_disable_request_draft(self):
        held, thread, results = self.begin_held('/bulk-drop')
        status, result = self.api_call('/api/intercept/bulk', {'action': 'drop'})
        self.assertEqual((status, result['count']), (200, 1))
        thread.join(5)
        self.assertEqual(results[0][0], 502)
        self.assertIn('Request dropped', self.engine.history()[0]['error'])
        held, thread, results = self.begin_held('/before-disable')
        draft = held['request'].replace('/before-disable', '/draft-disabled')
        self.api_call('/api/intercept', {'enabled': False, 'edits': {held['id']: {'request': draft}}})
        thread.join(5)
        self.assertEqual(results[0][1], b'fixture:/draft-disabled')

    def test_disable_releases_response_and_stop_drops(self):
        held, thread, results = self.begin_held('/release')
        self.api_call('/api/intercept/' + held['id'] + '/forward', {'intercept_response': True})
        self.assertEqual(self.pending()['stage'], 'response')
        response = self.pending()
        edited = response['response'].replace('fixture:/release', 'edited release')
        self.assertEqual(self.api_call('/api/intercept', {'enabled': False, 'edits': {response['id']: {'response': 'invalid'}}})[0], 400)
        self.assertTrue(self.engine.intercept)
        self.assertFalse(results)
        self.api_call('/api/intercept', {'enabled': False, 'edits': {response['id']: {'response': edited}}})
        thread.join(5)
        self.assertEqual(results[0][1], b'edited release')
        held, thread, results = self.begin_held('/stop')
        self.api_call('/api/intercept/' + held['id'] + '/forward', {'intercept_response': True})
        self.assertEqual(self.pending()['stage'], 'response')
        port = self.engine.proxy_port
        self.engine.set_proxy(False)
        thread.join(5)
        self.assertEqual(results[0][0], 502)
        self.assertNotIn(b'fixture:/stop', results[0][1])
        self.engine.set_proxy(True, port)

    def test_repeater_and_headers(self):
        status,item=self.api_call('/api/repeater',{'url':self.target,'request':'GET /hello HTTP/1.1\r\n\r\n'})
        self.assertEqual((status,item['status']),(200,200))
        self.assertIn('fixture:/hello',item['response'])
        self.assertEqual(item['response'].count('X-Duplicate:'),2)
        self.assertEqual(self.api_call('/api/history/'+str(item['id']))[1]['request'],item['request'])
    def test_proxy_binary_post(self):
        body=bytes(range(256)); status,data,_=self.proxy('/binary',body)
        self.assertEqual((status,data),(201,body))
    def test_proxy_chunked_post(self):
        status,data,_=self.proxy('/chunked',iter([b'hello',b' world']),True)
        self.assertEqual((status,data),(201,b'hello world'))
    def test_intercept_edit_and_drop(self):
        self.api_call('/api/intercept',{'enabled':True})
        result=[];thread=threading.Thread(target=lambda:result.append(self.proxy('/before')));thread.start()
        pending=self.pending()
        status,_=self.api_call('/api/intercept/'+pending['id']+'/forward',{'request':pending['request'].replace('/before','/after')})
        self.assertEqual(status,200);thread.join(5)
        self.assertEqual(result[0][1],b'fixture:/after')
        result=[];thread=threading.Thread(target=lambda:result.append(self.proxy('/drop')));thread.start()
        pending=self.pending();self.api_call('/api/intercept/'+pending['id']+'/drop',{});thread.join(5)
        self.assertEqual(result[0][0],502)
        self.api_call('/api/intercept',{'enabled':False})
    def test_connect_tunnel(self):
        self.engine.configure_tls({'passthrough':['127.0.0.1']})
        self.addCleanup(self.engine.configure_tls,{'passthrough':[]})
        sock=socket.create_connection(('127.0.0.1',self.engine.proxy_port),timeout=5)
        authority=f'127.0.0.1:{self.fixture.server_port}'
        sock.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
        response=b''
        while b'\r\n\r\n' not in response:response+=sock.recv(4096)
        self.assertIn(b'200 Connection Established',response)
        sock.sendall(f'GET /tunnel HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n'.encode())
        response=b''
        while True:
            data=sock.recv(4096)
            if not data:break
            response+=data
        sock.close();self.assertIn(b'fixture:/tunnel',response)
    def test_errors_and_origin_guard(self):
        self.assertEqual(self.api_call('/api/status',headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.api_call('/api/status',headers={'Host':'evil.example'})[0],403)
        _,item=self.api_call('/api/repeater',{'url':'file:///tmp/a','request':'GET / HTTP/1.1\n\n'})
        self.assertTrue(item['error']);self.assertEqual(item['status'],0)
        _,item=self.api_call('/api/repeater',{'url':self.target,'request':'broken'})
        self.assertTrue(item['error'])
    def test_api_token_required(self):
        self.assertEqual(self.api_call('/api/status',headers={'X-Dopamine-Token':None})[0],401)
        self.assertEqual(self.api_call('/api/status',headers={'X-Dopamine-Token':'wrong'})[0],401)
        self.assertEqual(self.api_call('/api/intercept',{'enabled':True},headers={'X-Dopamine-Token':None})[0],401)
        self.assertFalse(self.engine.intercept)
        self.assertEqual(self.api_call('/api/status',headers={'X-Dopamine-Token':None,'Cookie':'dopamine_api='+self.engine.api_token})[0],401)
    def test_head_length_and_repeater_newlines(self):
        conn=http.client.HTTPConnection('127.0.0.1',self.engine.proxy_port,timeout=5)
        conn.request('HEAD',self.target+'/head')
        r=conn.getresponse();self.assertEqual(r.getheader('Content-Length'),'123');self.assertEqual(r.read(),b'');conn.close()
        _,item=self.api_call('/api/repeater',{'url':self.target,'request':'POST / HTTP/1.1\r\n\r\na\r\nb'})
        self.assertTrue(item['response'].endswith('a\r\nb'))
    def test_stop_closes_active_tunnel(self):
        sock=socket.create_connection(('127.0.0.1',self.engine.proxy_port),timeout=5)
        authority=f'127.0.0.1:{self.fixture.server_port}'
        sock.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
        response=b''
        while b'\r\n\r\n' not in response:response+=sock.recv(4096)
        port=self.engine.proxy_port
        self.engine.set_proxy(False)
        self.assertEqual(sock.recv(1),b'')
        sock.close();self.engine.set_proxy(True,port)
    def test_history_clear(self):
        self.api_call('/api/repeater',{'url':self.target,'request':'GET /clear HTTP/1.1\r\n\r\n'})
        self.assertTrue(self.engine.history())
        self.assertEqual(self.api_call('/api/history/clear',{}),(200,{'ok':True}))
        self.assertEqual(self.engine.history(),[]);self.assertEqual(self.api_call('/api/status')[1]['history_count'],0)
    def test_status_and_persistence(self):
        self.assertEqual(self.api_call('/api/status')[1]['app'],'dopamine')
        other=Engine(self.temp.name)
        self.assertEqual(len(other.history()),len(self.engine.history()));other.db.close()

if __name__ == '__main__':unittest.main(verbosity=2)
