const assert=require('node:assert/strict');
const {parseCurl}=require('../ui/curl-import.js');
let count=0;function test(name,fn){fn();count++;console.log('ok '+name)}
test('Chromium multiline JSON and headers',()=>{const r=parseCurl(`curl 'https://example.test/api?q=a%20b' \\\n -H 'accept: application/json' \\\n -H 'content-type: application/json' \\\n -H 'authorization: Bearer local-fixture' \\\n --data-raw '{"name":"O’Connor","active":true}'`);assert.equal(r.url,'https://example.test/api?q=a%20b');assert.match(r.request,/^POST \/api\?q=a%20b HTTP\/1.1/);assert.match(r.request,/authorization: Bearer local-fixture/);assert.ok(r.request.endsWith('{"name":"O’Connor","active":true}'))});
test('ANSI quote body preserves CRLF',()=>{assert.ok(parseCurl("curl https://example.test --data-binary $'first\\r\\nsecond'").request.endsWith('first\r\nsecond'))});
test('Shell escaped apostrophes',()=>{assert.ok(parseCurl("curl https://example.test --data-raw 'it'\\''s fine'").request.endsWith("it's fine"))});
test('Repeated form fields, GET query, fragment',()=>{const r=parseCurl("curl -G 'https://example.test/a?x=1#fragment' -d a=2 --data-urlencode 'name=two words'");assert.equal(r.url,'https://example.test/a?x=1&a=2&name=two%20words');assert.match(r.request,/^GET /)});
test('Method override, duplicate headers, cookies',()=>{const r=parseCurl('curl --url=https://example.test -XPATCH -H"X-Tag: one" -H "X-Tag: two" -b "a=1; b=x=y" -d a=1');assert.match(r.request,/^PATCH /);assert.match(r.request,/X-Tag: one\r\nX-Tag: two/);assert.match(r.request,/Cookie: a=1; b=x=y/)});
test('UTF8 length and Basic auth',()=>{const r=parseCurl("curl https://example.test -u test:secret --data-raw '🌱'");assert.match(r.request,/Content-Length: 4/);assert.match(r.request,/Authorization: Basic dGVzdDpzZWNyZXQ=/)});
test('Inline @ for data-raw and empty body',()=>{assert.ok(parseCurl("curl https://example.test --data-raw '@literal'").request.endsWith('@literal'));assert.match(parseCurl("curl https://example.test -d ''").request,/^POST /)});
test('HEAD and compressed transport note',()=>{const r=parseCurl('curl -I https://example.test --compressed');assert.match(r.request,/^HEAD /);assert.ok(r.notes.length)});
test('Reject unsupported and malformed commands',()=>{for(const s of ["curl https://example.test -F a=@file","curl https://example.test --data @file","curl https://example.test | sh","curl 'unclosed","curl https://a.test https://b.test","wget https://example.test","curl https://example.test -H $'x: y\\r\\nz: injected'"]){assert.throws(()=>parseCurl(s),undefined,s)}});
test('JSON repeated fields concatenate',()=>{assert.ok(parseCurl(`curl https://example.test --json '{"a":' --json '1}'`).request.endsWith('{"a":1}'))});
console.log(count+' cURL importer tests passed');
