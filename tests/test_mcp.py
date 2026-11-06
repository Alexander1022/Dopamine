"""Loopback integration tests for built-in MCP and the stdio bridge."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from server import Engine, LocalServer, APIHandler
from test_engine import Fixture
from dopamine_mcp import PROTOCOLS, TOOLS, MCPService


class MCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.engine = Engine(cls.temp.name)
        cls.api = LocalServer(('127.0.0.1', 0), APIHandler)
        cls.api.engine = cls.engine
        cls.engine.api_port = cls.api.server_port
        cls.fixture = LocalServer(('127.0.0.1', 0), Fixture)
        for server in (cls.api, cls.fixture):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.target = 'http://127.0.0.1:' + str(cls.fixture.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.engine.set_proxy(False)
        for server in (cls.api, cls.fixture):
            server.shutdown()
            server.server_close()
        cls.engine.db.close()
        cls.temp.cleanup()

    def setUp(self):
        self.engine.mcp.configure({'enabled': True, 'read_only': False})

    def tearDown(self):
        self.engine.set_proxy(False)
        self.engine.intercept = False

    def http(self, message=None, headers=None, method='POST', raw=None, path='/mcp'):
        connection = http.client.HTTPConnection('127.0.0.1', self.api.server_port, timeout=6)
        h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
             'Authorization': 'Bearer ' + self.engine.mcp.token, 'MCP-Protocol-Version': PROTOCOLS[0]}
        h.update(headers or {})
        connection.request(method, path, raw if raw is not None else json.dumps(message or {}), h)
        response = connection.getresponse()
        body = response.read()
        result = response.status, json.loads(body) if body else None
        connection.close()
        return result

    def rpc(self, method, params=None, **kwargs):
        return self.http(dict(jsonrpc='2.0', id=1, method=method, params=params or {}), **kwargs)

    def call(self, name, **args):
        status, response = self.rpc('tools/call', dict(name='dopamine_' + name, arguments=args))
        self.assertEqual(status, 200, response)
        self.assertIn('result', response, response)
        return response['result']

    def data(self, name, **args):
        result = self.call(name, **args)
        self.assertFalse(result['isError'], result)
        return result['structuredContent']

    def test_initialize_discover_resources_and_notifications(self):
        for version in PROTOCOLS + ('future-version',):
            status, result = self.rpc('initialize', dict(protocolVersion=version, capabilities={}, clientInfo={'name': 'test', 'version': '1'}))
            self.assertEqual(status, 200)
            self.assertEqual(result['result']['protocolVersion'], version if version in PROTOCOLS else PROTOCOLS[0])
            self.assertEqual(result['result']['serverInfo']['name'], 'dopamine')
        tools = self.rpc('tools/list')[1]['result']['tools']
        self.assertEqual(len(tools), len(TOOLS))
        self.assertNotIn('tier', json.dumps(tools[0]))
        for tool in tools:
            self.assertIn('annotations', tool)
            self.assertIn('outputSchema', tool)
        self.assertEqual(self.http(dict(jsonrpc='2.0', method='notifications/initialized')), (202, None))
        self.assertEqual(self.http(dict(jsonrpc='2.0', method='notifications/cancelled', params={'requestId': 3})), (202, None))
        self.assertEqual(self.rpc('ping')[1]['result'], {})
        uri = self.rpc('resources/list')[1]['result']['resources'][0]['uri']
        guide = self.rpc('resources/read', {'uri': uri})[1]['result']['contents'][0]['text']
        self.assertIn('untrusted data', guide)

    def test_security_and_http_contract(self):
        self.assertEqual(self.rpc('tools/list', headers={'Authorization': ''})[0], 401)
        self.assertEqual(self.rpc('tools/list', headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.rpc('tools/list', headers={'Authorization': 'Bearer é'})[0], 401)
        self.assertEqual(self.rpc('tools/list', headers={'Origin': 'https://untrusted.example'})[0], 403)
        self.assertEqual(self.rpc('tools/list', headers={'Host': 'untrusted.example'})[0], 403)
        self.assertEqual(self.rpc('tools/list', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.rpc('tools/list', headers={'Accept': 'application/json'})[0], 406)
        self.assertEqual(self.rpc('tools/list', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.rpc('tools/list', headers={'MCP-Protocol-Version': 'bogus'})[0], 400)
        self.assertEqual(self.http(method='GET')[0], 405)
        self.assertEqual(self.http(method='DELETE')[0], 405)
        self.engine.mcp.configure({'enabled': False})
        self.assertEqual(self.rpc('tools/list')[0], 403)

    def test_protocol_and_tool_errors(self):
        self.assertEqual(self.http(raw='{broken')[1]['error']['code'], -32700)
        self.assertEqual(self.http(raw='NaN')[1]['error']['code'], -32700)
        for request in ([], {'jsonrpc': '1.0', 'id': 1}, {'jsonrpc': '2.0', 'id': True, 'method': 'ping'}):
            self.assertEqual(self.http(request, raw=json.dumps(request))[1]['error']['code'], -32600)
        self.assertEqual(self.rpc('unknown')[1]['error']['code'], -32601)
        self.assertEqual(self.rpc('tools/call', {'name': 'unknown'})[1]['error']['code'], -32602)
        for args in ({'limit': True}, {'limit': 0}, {'limit': 101}, {'query': 2}, {'unused': True}):
            result = self.call('list_history', **args)
            self.assertTrue(result['isError'], result)
        self.assertTrue(self.call('get_exchange', id=999999)['isError'])
        self.assertTrue(self.call('transform_text', text='%', operation='decode', format='url')['isError'])
        self.assertTrue(self.call('transform_text', text='invalid!', operation='decode')['isError'])

    def test_read_only_blocks_all_control_tools(self):
        self.engine.mcp.configure({'read_only': True})
        attempts = [('send_request', {'url': self.target}), ('set_proxy', {'running': True}),
                    ('set_intercept', {'enabled': True}), ('resolve_pending', {'id': 'unknown', 'action': 'forward'})]
        for name, args in attempts:
            result = self.call(name, **args)
            self.assertTrue(result['isError'])
            self.assertIn('Active access required', result['content'][0]['text'])
        self.assertTrue(self.data('get_status')['mcp_read_only'])
        self.assertEqual(self.data('transform_text', text='🌱', operation='encode')['output'], '8J+MsQ==')
        self.assertFalse(self.engine.proxy)

    def test_send_history_pagination_and_bounded_messages(self):
        for path in ('/mcp-search/one', '/mcp-search/two', '/mcp-search/three'):
            result = self.data('send_request', url=self.target + path)
            self.assertEqual(result['status'], 200)
            self.assertEqual(result['source'], 'mcp')
            self.assertIn('X-Duplicate: one\r\nX-Duplicate: two', result['response']['text'])
        page = self.data('list_history', query='/mcp-search/', source='mcp', method='get', status=200, limit=2)
        self.assertEqual(page['total_count'], 3)
        self.assertTrue(page['has_more'])
        self.assertNotIn('request', page['items'][0])
        tail = self.data('list_history', query='/mcp-search/', before_id=page['next_before_id'], limit=2)
        self.assertEqual(len(tail['items']), 1)
        self.assertFalse(tail['has_more'])
        ident = page['items'][0]['id']
        chunk = self.data('get_exchange', id=ident, max_chars=10, part='request')
        self.assertNotIn('response', chunk)
        self.assertTrue(chunk['request']['has_more'])
        next_page = self.data('get_exchange', id=ident, offset=chunk['request']['next_offset'], max_chars=10, part='request')
        self.assertEqual(chunk['request']['text'] + next_page['request']['text'], self.engine.history(ident)['request'][:20])
        targets = self.data('list_targets')
        self.assertIn(self.target, [t['origin'] for t in targets['items']])
        self.assertEqual(self.data('list_history', query='%')['total_count'], 0)

    def test_raw_post_and_request_validation(self):
        raw = 'POST /raw-mcp HTTP/1.1\r\nContent-Type: text/plain\r\nContent-Length: 999\r\n\r\nhello'
        result = self.data('send_request', url=self.target, request=raw)
        self.assertEqual(result['status'], 201)
        self.assertTrue(result['response']['text'].endswith('hello'))
        self.assertIn('Content-Length: 5', result['request']['text'])
        self.assertTrue(self.call('send_request', url=self.target, request=raw, method='POST')['isError'])
        self.assertTrue(self.call('send_request', url='file:///etc/passwd')['isError'])
        before = len(self.engine.history())
        for host in ('127.0.0.1', 'localhost', '[::1]', '0.0.0.0', '[::ffff:127.0.0.1]'):
            result = self.call('send_request', url=f'http://{host}:{self.api.server_port}/api/status')
            self.assertTrue(result['isError'], host)
            self.assertIn('Dopamine itself', json.dumps(result))
        self.assertTrue(self.call('send_request', url=f'http://127.0.0.1:{self.engine.proxy_port}/')['isError'])
        self.assertEqual(len(self.engine.history()), before)
        self.assertTrue(self.call('send_request', url=self.target, headers=[{'name': 'X-Test', 'value': 'a\r\nBad: true'}])['isError'])
        self.assertEqual(self.data('send_request', url=self.target, method='POST', body='hello')['status'], 201)

    def test_reading_a_failed_exchange_is_successful(self):
        failed = self.call('send_request', url=self.target, request='CONNECT example.test:443 HTTP/1.1\r\n\r\n')
        self.assertTrue(failed['isError'])
        captured = self.data('get_exchange', id=failed['structuredContent']['id'])
        self.assertEqual(captured['status'], 0)
        self.assertIn('CONNECT', captured['error'])

    def start_proxy(self):
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        self.data('set_proxy', running=True, port=port)
        self.data('set_intercept', enabled=True)

    def wait_pending(self, stage):
        for _ in range(200):
            items = self.data('list_pending')['items']
            matching = [p for p in items if p['stage'] == stage]
            if matching:
                return matching[0]
            time.sleep(.01)
        self.fail('No held ' + stage)

    def proxy_request(self, result):
        connection = http.client.HTTPConnection('127.0.0.1', self.engine.proxy_port, timeout=5)
        connection.request('GET', self.target + '/mcp-held')
        response = connection.getresponse()
        result.append((response.status, response.read()))
        connection.close()

    def test_intercept_request_and_response_workflow(self):
        self.start_proxy()
        result = []
        thread = threading.Thread(target=self.proxy_request, args=(result,)); thread.start()
        pending = self.wait_pending('request')
        self.assertNotIn('request', pending)
        held = self.data('get_pending', id=pending['id'])
        self.assertIn('GET ', held['request']['text'])
        self.assertTrue(self.call('resolve_pending', id=pending['id'], action='forward', response='wrong stage')['isError'])
        self.data('resolve_pending', id=pending['id'], action='forward', request='GET /mcp-edited HTTP/1.1\r\n\r\n', intercept_response=True)
        response = self.wait_pending('response')
        self.assertIn('fixture:/mcp-edited', self.data('get_pending', id=response['id'])['response']['text'])
        self.assertFalse(result)
        self.data('resolve_pending', id=response['id'], action='forward', response='HTTP/1.1 202 Accepted\r\n\r\nagent-edited')
        thread.join(5)
        self.assertEqual(result, [(202, b'agent-edited')])
        self.assertTrue(self.call('resolve_pending', id=response['id'], action='forward')['isError'])

    def test_drop_and_explicit_bulk_acknowledgement(self):
        self.start_proxy()
        result = []
        thread = threading.Thread(target=self.proxy_request, args=(result,)); thread.start()
        pending = self.wait_pending('request')
        self.assertTrue(self.call('set_proxy', running=False)['isError'])
        self.assertTrue(self.call('set_intercept', enabled=False)['isError'])
        self.assertTrue(self.call('resolve_pending', id=pending['id'], action='drop', request='ignored')['isError'])
        self.data('resolve_pending', id=pending['id'], action='drop')
        thread.join(5)
        self.assertEqual(result[0][0], 502)
        self.assertTrue(self.data('set_intercept', enabled=False)['intercept'] is False)

    def test_persistence_activity_and_config(self):
        self.engine.mcp.configure({'read_only': True})
        self.assertTrue(MCPService(self.engine, self.temp.name).read_only)
        self.assertEqual(MCPService(self.engine, self.temp.name).token, self.engine.mcp.token)
        self.assertEqual(os.stat(self.engine.mcp.token_path).st_mode & 0o777, 0o600)
        self.assertTrue(self.call('set_proxy', running=True)['isError'])
        self.data('transform_text', text='PRIVATE_CONTENT', operation='sha256')
        settings = self.engine.mcp.settings(self.api.server_port)
        self.assertNotIn('PRIVATE_CONTENT', json.dumps(settings))
        self.assertNotIn(self.engine.mcp.token, json.dumps(settings))
        self.assertTrue(settings['activity'][0]['ok'])
        self.assertFalse(settings['activity'][1]['ok'])
        config = json.loads(settings['configs']['claude'])
        self.assertEqual(config['mcpServers']['dopamine']['command'], '/usr/bin/python3')
        self.assertIn('--port', config['mcpServers']['dopamine']['args'])

    def test_acknowledged_release_and_stop(self):
        self.start_proxy()
        result = []
        thread = threading.Thread(target=self.proxy_request, args=(result,)); thread.start()
        self.wait_pending('request')
        self.data('set_intercept', enabled=False, release_held=True)
        thread.join(5)
        self.assertEqual(result, [(200, b'fixture:/mcp-held')])
        self.data('set_intercept', enabled=True)
        result = []
        thread = threading.Thread(target=self.proxy_request, args=(result,)); thread.start()
        self.wait_pending('request')
        self.data('set_proxy', running=False, drop_held=True)
        thread.join(5)
        self.assertEqual(result[0][0], 502)

    def test_stdio_bridge_and_check(self):
        script = str(Path(__file__).resolve().parents[1] / 'engine' / 'dopamine_mcp.py')
        command = ['/usr/bin/python3', script, '--port', str(self.api.server_port), '--data-dir', self.temp.name]
        messages = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion=PROTOCOLS[0], capabilities={}, clientInfo={'name': 'stdio-test', 'version': '1'})),
                    dict(jsonrpc='2.0', method='notifications/initialized'),
                    dict(jsonrpc='2.0', id=2, method='tools/list'),
                    dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='dopamine_transform_text', arguments={'text': 'hello', 'operation': 'encode'}))]
        completed = subprocess.run(command, input=''.join(json.dumps(m) + '\n' for m in messages), text=True, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([r['id'] for r in lines], [1, 2, 3])
        self.assertEqual(len(lines[1]['result']['tools']), len(TOOLS))
        self.assertEqual(lines[2]['result']['structuredContent']['output'], 'aGVsbG8=')
        check = subprocess.run(command + ['--check'], text=True, capture_output=True, timeout=5)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout)['tools'], len(TOOLS))


if __name__ == '__main__':
    unittest.main()
