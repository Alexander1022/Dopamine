"""Projects, migration, scope, search, decompression, match-and-replace and MCP access tiers."""
import gzip
import http.client
import json
import sqlite3
import socket
import tempfile
import threading
import time
import unittest
import zlib
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from server import Engine, LocalServer, APIHandler
from project import Project, clean_rule
from rewrite import apply_rules, decode_body


class Echo(BaseHTTPRequestHandler):
    """Echoes the request headers as JSON; gzips the reply when the client accepts it or the path starts with /gz."""
    def do_GET(self):
        body = json.dumps({'path': self.path, 'headers': dict(self.headers), 'secret': 'token-abc'}).encode()
        compress = 'gzip' in self.headers.get('Accept-Encoding', '') or self.path.startswith('/gz')
        if compress:
            body = gzip.compress(body)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        if compress:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class UnitTests(unittest.TestCase):
    def test_decode_body(self):
        data = b'{"hello": "world"}' * 20
        headers, body, note = decode_body([('Content-Encoding', 'gzip'), ('Content-Length', '9')], gzip.compress(data))
        self.assertEqual((body, note), (data, 'gzip decoded'))
        self.assertEqual(headers, [])
        raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        self.assertEqual(decode_body([('Content-Encoding', 'deflate')], raw.compress(data) + raw.flush())[1], data)
        self.assertEqual(decode_body([('Content-Encoding', 'deflate')], zlib.compress(data))[1], data)
        headers, body, note = decode_body([('Content-Encoding', 'br')], b'\x8b\x00')
        self.assertEqual((body, note), (b'\x8b\x00', 'br (not decoded)'))
        self.assertEqual(headers, [('Content-Encoding', 'br')])
        self.assertIn('could not decode', decode_body([('Content-Encoding', 'gzip')], b'not gzip')[2])

    def test_rules(self):
        rules = [clean_rule(dict(target='request_header', match='', replace='X-Added: 1')),
                 clean_rule(dict(target='request_header', match='Cookie: secret', replace='')),
                 clean_rule(dict(target='request_body', match=r'user=(\w+)', replace='user=$1-x$$', regex=True)),
                 clean_rule(dict(target='response_line', match='200 OK', replace='299 Rewritten', all_traffic=True))]
        first, headers, body, changed = apply_rules(rules, 'request', 'POST / HTTP/1.1', [('Cookie', 'secret'), ('Host', 'a')], b'user=bob', True)
        self.assertTrue(changed)
        self.assertEqual(headers, [('Host', 'a'), ('X-Added', '1')])
        self.assertEqual(body, b'user=bob-x$')
        self.assertFalse(apply_rules(rules, 'request', 'GET / HTTP/1.1', [], b'', False)[3], 'in-scope rules skip out-of-scope traffic')
        self.assertEqual(apply_rules(rules, 'response', 'HTTP/1.1 200 OK', [], b'', False)[0], 'HTTP/1.1 299 Rewritten')
        disabled = [dict(rules[0], enabled=False)]
        self.assertFalse(apply_rules(disabled, 'request', 'GET / HTTP/1.1', [], b'', True)[3])
        for bad in (dict(target='request_body', match='(', regex=True), dict(target='nowhere', match='a'), dict(target='request_body', match=''),
                    dict(target='request_header', match='a', replace='b\r\nInjected: 1')):
            with self.assertRaises(ValueError):
                clean_rule(bad)
        broken = [clean_rule(dict(target='request_header', match='Host: a', replace='not a header'))]
        with self.assertRaises(ValueError):
            apply_rules(broken, 'request', 'GET / HTTP/1.1', [('Host', 'a')], b'', True)

    def test_scope_matching(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Project(Path(temp) / 'scope.dopamine')
            self.assertTrue(project.in_scope('https://anything.test/'), 'empty scope includes everything')
            project.set_scope(include=['*.acme.test', 'api.other.test /v2/'], exclude=[{'host': 'cdn.acme.test'}])
            for url, expected in (('https://acme.test/', True), ('https://www.acme.test/x', True), ('https://cdn.acme.test/', False),
                                  ('https://api.other.test/v2/users', True), ('https://api.other.test/v1/users', False),
                                  ('https://evil-acme.test/', False), ('not a url', False)):
                self.assertEqual(project.in_scope(url), expected, url)
            with self.assertRaises(ValueError):
                project.set_scope(include=['bad host/'])
            with self.assertRaises(ValueError):
                project.set_scope(include=[{'host': 'a.test', 'path': 'no-slash'}])
            reopened = Project(project.path)
            self.assertEqual(reopened.scope['exclude'], [{'host': 'cdn.acme.test', 'path': ''}])
            project.close()
            reopened.close()

    def test_legacy_migration_and_tab_import(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy = sqlite3.connect(str(Path(temp) / 'history.sqlite3'))
            legacy.execute('CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)')
            item = dict(method='GET', url='https://old.test/a', status=200, length=2, duration_ms=5, source='proxy', time=1.0, error='', tunnel=False,
                        request='GET /a HTTP/1.1\r\nHost: old.test\r\n\r\n', response='HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nhi',
                        response_body_base64='aGk=')
            legacy.execute('INSERT INTO history (data) VALUES (?)', (json.dumps(item),))
            legacy.execute('INSERT INTO history (data) VALUES (?)', ('not json',))
            legacy.commit()
            legacy.close()
            engine = Engine(temp)
            self.assertEqual(engine.project.path, Path(temp) / 'Projects' / 'Default.dopamine')
            self.assertFalse((Path(temp) / 'history.sqlite3').exists())
            self.assertTrue((Path(temp) / 'history.sqlite3.migrated').exists())
            saved = engine.history(engine.history()[0]['id'])
            self.assertEqual((saved['url'], saved['response_body_base64'], saved['host']), ('https://old.test/a', 'aGk=', 'old.test'))
            self.assertTrue(engine.status()['project']['legacy_tabs'])
            self.assertTrue(engine.project.import_legacy_tabs([{'id': 'a', 'url': 'https://old.test/', 'request': 'GET / HTTP/1.1\r\n\r\n'}]))
            self.assertFalse(engine.project.import_legacy_tabs([{'id': 'b', 'url': '', 'request': ''}]), 'tabs import only once')
            self.assertEqual([t['id'] for t in engine.project.tabs()], ['a'])
            engine.project.close()
            restarted = Engine(temp)
            self.assertEqual(restarted.project.name, 'Default')
            self.assertEqual(len(restarted.history()), 1)
            restarted.project.close()

    def test_not_a_project(self):
        with tempfile.TemporaryDirectory() as temp:
            other = sqlite3.connect(str(Path(temp) / 'other.dopamine'))
            other.execute('CREATE TABLE something (x)')
            other.close()
            with self.assertRaises(ValueError):
                Project(Path(temp) / 'other.dopamine')


class EngineProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.engine = Engine(cls.temp.name)
        cls.fixture = LocalServer(('127.0.0.1', 0), Echo)
        cls.api = LocalServer(('127.0.0.1', 0), APIHandler)
        cls.api.engine = cls.engine
        cls.engine.api_port = cls.api.server_port
        for server in (cls.fixture, cls.api):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.engine.set_proxy(True, free_port())
        cls.target = f'http://127.0.0.1:{cls.fixture.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.engine.set_proxy(False)
        for server in (cls.fixture, cls.api):
            server.shutdown()
            server.server_close()
        cls.engine.project.close()
        cls.temp.cleanup()

    def setUp(self):
        self.engine.open_project(Path(self.temp.name) / ('case-' + self.id().rsplit('.', 1)[1]), create=True)
        self.engine.mcp.configure({'enabled': True, 'access': 'read'})

    def tearDown(self):
        self.engine.intercept = False
        self.engine.release_pending('drop')

    def api_call(self, path, data=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.api.server_port, timeout=5)
        headers = {'Content-Type': 'application/json', 'X-Dopamine-Token': self.engine.api_token}
        conn.request('POST' if data is not None else 'GET', path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()))
        conn.close()
        return result

    def proxy(self, path, headers=None, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.engine.proxy_port, timeout=6)
        conn.request('GET', (host or self.target) + path, headers=headers or {})
        response = conn.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        conn.close()
        return result

    def call(self, tool, **args):
        return self.engine.mcp.call_tool('dopamine_' + tool, args)

    def test_decoding_rules_and_originals(self):
        self.api_call('/api/rules', {'rules': [dict(target='request_header', match='', replace='X-Rule: on'),
                                               dict(target='response_body', match='token-(\\w+)', replace='REDACTED-$1', regex=True)]})
        status, body, headers = self.proxy('/gz/one', {'Accept-Encoding': 'gzip'})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['secret'], 'REDACTED-abc')
        self.assertEqual(data['headers'].get('X-Rule'), 'on')
        self.assertNotIn('Accept-Encoding', data['headers'], 'Accept-Encoding is stripped by default')
        self.assertNotIn('Content-Encoding', headers)
        self.assertEqual(int(headers['Content-Length']), len(body))
        item = self.engine.history(self.engine.history()[0]['id'])
        self.assertTrue(item['rewritten'])
        self.assertEqual(item['encoding'], 'gzip decoded')
        self.assertIn('token-abc', item['original_response'])
        self.assertIn('Accept-Encoding: gzip', item['original_request'])
        self.assertIn('REDACTED-abc', item['response'])
        # Repeater sends exactly what it is given: no rules, Accept-Encoding kept, response still decoded for display.
        result, _, _ = self.engine.exchange(self.target + '/plain', 'GET /plain HTTP/1.1\r\nAccept-Encoding: gzip\r\n\r\n')
        self.assertFalse(result.get('rewritten'))
        self.assertIn('token-abc', result['response'])
        self.assertEqual(result['encoding'], 'gzip decoded')

    def test_scope_controls_intercept_rules_and_recording(self):
        self.api_call('/api/scope', {'include': ['127.0.0.1']})
        self.api_call('/api/rules', {'rules': [dict(target='request_header', match='', replace='X-Rule: on')]})
        self.engine.intercept = True
        status, body, _ = self.proxy('/outside', host=f'http://localhost:{self.fixture.server_port}')
        self.assertEqual(status, 200, 'out-of-scope traffic passes Intercept')
        self.assertNotIn('X-Rule', json.loads(body)['headers'])
        results = []
        thread = threading.Thread(target=lambda: results.append(self.proxy('/inside')))
        thread.start()
        for _ in range(100):
            if self.engine.pending:
                break
            time.sleep(0.02)
        self.assertEqual(len(self.engine.pending), 1, 'in-scope requests are held')
        self.engine.bulk_resolve('forward')
        thread.join(5)
        self.assertEqual(results[0][0], 200)
        self.engine.intercept = False
        self.assertEqual(self.api_call('/api/history?scope=1')[1]['total_count'], 1)
        self.assertEqual(self.api_call('/api/history')[1]['total_count'], 2)
        self.api_call('/api/scope', {'record_out_of_scope': False})
        self.proxy('/dropped', host=f'http://localhost:{self.fixture.server_port}')
        self.assertEqual(self.api_call('/api/history')[1]['total_count'], 2)

    def test_search_and_paging(self):
        for path in ('/gz/alpha', '/beta', '/Gamma'):
            self.proxy(path)
        page = lambda query: self.api_call('/api/history?' + query)[1]
        self.assertEqual(page('search=token-abc')['total_count'], 3)
        self.assertEqual(page('search=GAMMA')['total_count'], 1)
        self.assertEqual(page('search=GAMMA&case=1')['total_count'], 0)
        self.assertEqual(page('search=/gz/al&in=request')['total_count'], 1)
        self.assertEqual(page('search=al&in=response')['total_count'], 1, 'two-character searches scan without the trigram index')
        self.assertEqual(page('search=%2F(beta%7Cgamma)&regex=1')['total_count'], 2)
        self.assertEqual(self.api_call('/api/history?search=(&regex=1')[0], 400)
        first = page('limit=2')
        self.assertTrue(first['has_more'])
        older = page('limit=2&before_id=' + str(first['items'][-1]['id']))
        self.assertEqual(len(older['items']), 1)
        self.engine.project.clear()
        self.assertEqual(page('search=token')['total_count'], 0)

    def test_project_switching(self):
        first = self.engine.project.path
        self.api_call('/api/scope', {'include': ['first.test']})
        self.api_call('/api/repeater/tabs', {'tabs': [{'id': 'one', 'url': 'https://first.test/', 'request': 'GET / HTTP/1.1\r\n\r\n'}]})
        self.engine.intercept = True
        thread = threading.Thread(target=lambda: self.proxy('/held', host='http://first.test:%d' % self.fixture.server_port))
        thread.start()
        for _ in range(100):
            if self.engine.pending:
                break
            time.sleep(0.02)
        status, body = self.api_call('/api/project', {'action': 'new'})
        self.assertEqual((status, body['held']), (409, 1))
        status, info = self.api_call('/api/project', {'action': 'new', 'held': 'drop'})
        self.assertEqual(status, 200)
        thread.join(5)
        self.assertTrue(info['name'].startswith('Untitled '))
        self.assertEqual(self.api_call('/api/scope')[1]['include'], [])
        self.assertEqual(self.api_call('/api/repeater/tabs')[1]['tabs'], [])
        status, moved = self.api_call('/api/project', {'action': 'move', 'path': str(Path(self.temp.name) / 'Named')})
        self.assertEqual((status, moved['name']), (200, 'Named'))
        self.assertTrue(Path(moved['path']).exists())
        status, back = self.api_call('/api/project', {'action': 'open', 'path': str(first)})
        self.assertEqual(back['tab_count'], 1)
        self.assertEqual(self.api_call('/api/scope')[1]['include'], [{'host': 'first.test', 'path': ''}])
        self.assertIn(str(Path(self.temp.name) / 'Named.dopamine'), [r['path'] for r in back['recent']])
        self.assertEqual(self.api_call('/api/project', {'action': 'open', 'path': '/nonexistent/x.dopamine'})[0], 404)
        self.assertEqual(self.api_call('/api/project', {'action': 'new', 'path': str(first)})[0], 400)

    def test_mcp_tiers(self):
        self.api_call('/api/scope', {'include': ['127.0.0.1']})
        denied = self.call('create_rule', target='request_header', replace='X-A: 1')
        self.assertIn('Workspace access required', denied['content'][0]['text'])
        self.engine.mcp.configure({'access': 'workspace'})
        rule = self.call('create_rule', target='request_header', replace='X-A: 1', comment='drafted')['structuredContent']
        self.assertEqual((rule['enabled'], rule['agent']), (False, True))
        self.assertIn('cannot turn rules on', self.call('update_rule', id=rule['id'], enabled=True)['content'][0]['text'])
        self.assertFalse(self.call('update_rule', id=rule['id'], match='', comment='still off')['isError'])
        # A person enabling the rule in the UI keeps its agent marker.
        rules = self.api_call('/api/rules')[1]['rules']
        rules[0]['enabled'] = True
        self.assertEqual(self.api_call('/api/rules', {'rules': rules})[1]['rules'][0]['agent'], True)
        # Editing a rule a person turned on switches it off again, so an agent cannot change live rewriting.
        self.api_call('/api/rules', {'rules': [dict(target='request_header', match='', replace='X-User: 1')]})
        user_rule = self.api_call('/api/rules')[1]['rules'][0]
        edited = self.call('update_rule', id=user_rule['id'], replace='X-Agent: 1')['structuredContent']
        self.assertEqual((edited['enabled'], edited['agent']), (False, True))
        added = self.call('add_to_scope', host='*.agent.test')['structuredContent']
        self.assertEqual(added['include'][-1], {'host': '*.agent.test', 'path': '', 'agent': True})
        tab = self.call('create_repeater_tab', url=self.target + '/tab', request='GET /tab HTTP/1.1\r\n\r\n', name='staged')['structuredContent']
        self.assertTrue(self.engine.project.tab(tab['id'])['agent'])
        self.assertIn('Active access required', self.call('send_repeater_tab', id=tab['id'])['content'][0]['text'])
        self.engine.mcp.configure({'access': 'active'})
        sent = self.call('send_repeater_tab', id=tab['id'])['structuredContent']
        self.assertEqual(sent['status'], 200)
        stored = self.engine.project.tab(tab['id'])
        self.assertEqual(len(stored['versions']), 1)
        self.assertIn('/tab', stored['response'])
        outside = self.call('send_request', url=f'http://localhost:{self.fixture.server_port}/x')
        self.assertIn('outside the project scope', outside['content'][0]['text'])
        self.assertFalse(self.call('send_request', url=f'http://localhost:{self.fixture.server_port}/x', allow_out_of_scope=True)['isError'])
        found = self.call('search_history', text='/tab')['structuredContent']
        self.assertEqual(found['total_count'], 1)
        everything = self.call('list_history', include_out_of_scope=True)['structuredContent']
        self.assertEqual(everything['total_count'], 2)
        self.assertEqual(self.call('list_history')['structuredContent']['total_count'], 1)
        note = self.call('annotate_exchange', id=found['items'][0]['id'], color='red', note='look here')['structuredContent']
        self.assertEqual((note['color'], note['note']), ('red', 'look here'))
        self.assertEqual(self.engine.history(note['id'])['annotated_by'], 'agent')
        self.assertTrue(self.call('get_exchange', id=note['id'], version='original')['isError'], 'unrewritten exchanges have no original')
        targets = self.call('list_targets', paths=True)['structuredContent']['items']
        self.assertEqual(targets[0]['paths'][0]['path'], '/tab')
        activity = self.engine.mcp.settings(self.api.server_port)['activity']
        self.assertEqual((activity[0]['tool'], activity[0]['tier']), ('dopamine_list_targets', 'read'))
        self.assertTrue(any(a['summary'] == 'host *.agent.test' for a in activity))

    def test_everything_tier_lifts_guardrails(self):
        self.api_call('/api/scope', {'include': ['127.0.0.1']})
        self.engine.mcp.configure({'access': 'active'})
        self.assertIn('cannot turn rules on', self.call('create_rule', target='request_header', replace='X-A: 1', enabled=True)['content'][0]['text'])
        self.assertEqual(self.api_call('/api/mcp/settings', {'access': 'everything'})[1]['access'], 'everything')
        rule = self.call('create_rule', target='request_header', replace='X-Everything: 1', enabled=True)['structuredContent']
        self.assertEqual((rule['enabled'], rule['agent']), (True, True))
        edited = self.call('update_rule', id=rule['id'], comment='kept on')['structuredContent']
        self.assertTrue(edited['enabled'], 'Everything edits keep the rule state')
        self.assertFalse(self.call('update_rule', id=rule['id'], enabled=False)['structuredContent']['enabled'])
        drafted = self.call('create_rule', target='request_header', replace='X-Draft: 1')['structuredContent']
        self.assertFalse(drafted['enabled'], 'rules still start off unless the agent asks')
        outside = self.call('send_request', url=f'http://localhost:{self.fixture.server_port}/anywhere')
        self.assertFalse(outside['isError'], outside)
        self.assertIn('Agents cannot send requests to Dopamine itself',
                      self.call('send_request', url=f'http://127.0.0.1:{self.api.server_port}/api/status')['content'][0]['text'])
        sent = self.engine.mcp.history(kind='send')['items']
        self.assertEqual(sent[1]['action'], 'Sent GET request (out of scope)')
        self.assertEqual(sent[1]['details']['access'], 'everything')

    def test_agent_history(self):
        self.api_call('/api/scope', {'include': ['127.0.0.1']})
        self.engine.mcp.configure({'access': 'active'})
        self.call('search_history', text='needle')
        sent = self.engine.mcp.call_tool('dopamine_send_request', dict(url=self.target + '/agent?q=1', method='POST', body='PRIVATE_BODY',
                                                                        headers=[{'name': 'Authorization', 'value': 'Bearer PRIVATE_TOKEN'}]), 'Claude Code 2.1')
        exchange = sent['structuredContent']['id']
        self.call('get_exchange', id=exchange)
        self.call('get_exchange', id=999999)
        self.call('transform_text', text='PRIVATE_TEXT', operation='encode')
        status, page = self.api_call('/api/mcp/history?limit=2')
        self.assertEqual(status, 200)
        self.assertEqual((page['total_count'], len(page['items']), page['has_more']), (5, 2, True))
        self.assertNotIn('PRIVATE', json.dumps(self.engine.mcp.history(limit=50)))
        failed = page['items'][1]
        self.assertEqual((failed['tool'], failed['ok'], failed['action']), ('dopamine_get_exchange', False, 'Read exchange #999999'))
        self.assertTrue(failed['error'])
        rest = self.api_call('/api/mcp/history?before_id=' + str(page['next_before_id']))[1]['items']
        read, send, search = rest
        self.assertEqual((read['exchange_id'], read['url'], read['status']), (exchange, self.target + '/agent?q=1', sent['structuredContent']['status']))
        self.assertEqual((send['action'], send['method'], send['url'], send['exchange_id'], send['client']),
                         ('Sent POST request', 'POST', self.target + '/agent?q=1', exchange, 'Claude Code 2.1'))
        self.assertEqual(send['details']['headers'], ['Authorization'])
        self.assertEqual(send['details']['body'], '<12 characters>')
        self.assertEqual(search['action'], 'Searched history for “needle” · 0 of 0')
        self.assertEqual(page['clients'], ['Claude Code 2.1'])
        self.assertEqual(self.api_call('/api/mcp/history?kind=failed')[1]['total_count'], 1)
        self.assertEqual(self.api_call('/api/mcp/history?client=Claude%20Code%202.1')[1]['total_count'], 1)
        self.assertEqual(self.api_call('/api/mcp/history?q=needle')[1]['total_count'], 1)
        self.assertEqual(self.api_call('/api/mcp/history?kind=bogus')[0], 400)
        # History lives in the project file and follows project switches.
        path = self.engine.project.path
        self.engine.open_project(Path(self.temp.name) / 'agent-history-other', create=True)
        self.assertEqual(self.engine.mcp.history()['total_count'], 0)
        self.engine.open_project(path)
        self.assertEqual(self.engine.mcp.history()['total_count'], 5)
        self.assertEqual(self.api_call('/api/mcp/history/clear', {})[1], {'ok': True})
        self.assertEqual(self.engine.mcp.history()['total_count'], 0)

    def test_legacy_read_only_setting(self):
        (Path(self.temp.name) / 'mcp-settings.json').write_text(json.dumps({'enabled': True, 'read_only': False}))
        from dopamine_mcp import MCPService
        self.assertEqual(MCPService(self.engine, self.temp.name).access, 'active')
        self.engine.mcp.configure({'read_only': True})
        self.assertEqual(self.engine.mcp.access, 'read')


if __name__ == '__main__':
    unittest.main()
