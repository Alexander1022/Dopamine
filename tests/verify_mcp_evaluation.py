"""Seed a temporary evaluation capture and verify ten read-only MCP workflows.

All records are explicit test fixtures. Nothing is written to the desktop app's history.
"""
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from server import Engine


def seed(engine):
    records = [
        ('POST', 'https://auth.dopamine.test/session', 200, 'proxy', 'X-Actor: qa-viewer\r\n', '{"login":"qa"}', '{"authenticated":true}'),
        ('GET', 'https://shop.dopamine.test/catalog', 200, 'proxy', '', '', '{"revision":"r1"}'),
        ('POST', 'https://shop.dopamine.test/cart', 201, 'repeater', '', '{"sku":"MOSS-10"}', '{"cart":"one"}'),
        ('POST', 'https://shop.dopamine.test/cart', 409, 'repeater', '', '{"sku":"OLIVE-42"}', '{"error":"OUT_OF_STOCK"}'),
        ('GET', 'https://files.dopamine.test/export', 200, 'mcp', '', '', '{"label":"aW52ZW50b3J5LW9jdG9iZXI=","padding":"' + 'x' * 17000 + '","X-Export-Checksum":"leaf-778"}'),
        ('GET', 'https://broken.dopamine.test/health', 0, 'mcp', '', '', 'Request failed: certificate verify failed'),
        ('POST', 'https://shop.dopamine.test/cart', 201, 'mcp', '', '{"sku":"OLIVE-42"}', '{"cart":"two"}'),
        ('GET', 'https://shop.dopamine.test/catalog', 200, 'mcp', '', '', '{"next":"%2Fcatalog%3Fcursor%3Dolive%2Bgreen"}'),
    ]
    for index, (method, url, status, source, headers, body, response) in enumerate(records):
        path = '/' + url.split('/', 3)[-1]
        extra = 'X-Revision: r2\r\n' if index == 7 else ''
        request = method + ' ' + path + ' HTTP/1.1\r\n' + extra + '\r\n' + body
        raw = ('HTTP/1.1 ' + str(status) + ' Fixture\r\n' + headers + '\r\n' + response) if status else response
        engine.save(dict(method=method, url=url, status=status, source=source, request=request, response=raw,
                         time=1792713600 + index, duration_ms=10 + index, length=len(response), tunnel=False,
                         error='certificate verify failed' if not status else ''))


def verify(engine):
    def call(name, **args):
        result = engine.mcp.call_tool('dopamine_' + name, args)
        assert not result['isError'], result
        return result['structuredContent']
    def history(**args):
        result, cursor = [], None
        while True:
            page = call('list_history', limit=2, **dict(args, **({'before_id': cursor} if cursor else {})))
            result.extend(page['items'])
            if not page['has_more']:
                return result
            cursor = page['next_before_id']
    def raw(ident, part):
        pieces, offset = [], 0
        while True:
            page = call('get_exchange', id=ident, part=part, offset=offset, max_chars=2000)[part]
            pieces.append(page['text'])
            if not page['has_more']:
                return ''.join(pieces)
            offset = page['next_offset']
    def body(ident, part):
        return json.loads(raw(ident, part).split('\r\n\r\n', 1)[1])
    auth = history(query='/session', status=200)[0]
    actor = raw(auth['id'], 'response').split('X-Actor: ', 1)[1].split('\r\n')[0]
    rejected = history(query='/cart', status=409)[0]
    error = body(rejected['id'], 'response')['error']
    export = history(query='/export')[0]
    export_body = body(export['id'], 'response')
    label = call('transform_text', text=export_body['label'], operation='decode', format='base64')['output']
    catalogs = history(query='/catalog')
    assert 'X-Revision:' not in raw(catalogs[-1]['id'], 'request')
    revision = raw(catalogs[0]['id'], 'request').split('X-Revision: ', 1)[1].split('\r\n')[0]
    failure = history(status=0)[0]
    assert 'certificate' in raw(failure['id'], 'response')
    method = failure['method']
    targets = call('list_targets')['items']
    origin = max(targets, key=lambda t: t['count'])['origin']
    assert len(history(query=origin)) == max(t['count'] for t in targets)
    newest_cart = history(query='/cart', status=201)[0]
    sku = body(newest_cart['id'], 'request')['sku']
    rejected_sku = body(rejected['id'], 'request')['sku']
    count = sum(body(row['id'], 'request')['sku'] == rejected_sku for row in history(query='/cart', method='POST'))
    checksum = export_body['X-Export-Checksum']
    encoded = body(catalogs[0]['id'], 'response')['next']
    link = call('transform_text', text=encoded, operation='decode', format='url')['output']
    answers = [actor, error, label, revision, method, origin, sku, str(count), checksum, link]
    expected = [qa.findtext('answer') for qa in ET.parse(Path(__file__).with_name('mcp-evaluation.xml')).getroot()]
    assert answers == expected, (answers, expected)
    print(json.dumps(dict(verified=len(answers), answers=answers)))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder:
        engine = Engine(folder)
        try:
            seed(engine)
            verify(engine)
        finally:
            engine.db.close()
