const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/../ui/index.html','utf8');const start=html.indexOf('function pendingEdit('),end=html.indexOf('\nfunction renderPending',start);const drafts=new Map();const c={interceptDrafts:drafts};vm.createContext(c);vm.runInContext(html.slice(start,end),c);
const request={id:'a',stage:'request',url:'http://local.test/',request:'POST / HTTP/1.1\r\n\r\na\r\nb'};
drafts.set('a',{raw:'POST / HTTP/1.1\n\na\nb',url:request.url,intercept_response:true});assert.equal(c.pendingEdit(request).request,undefined,'display newline normalization must not count as an edit');assert.equal(c.pendingEdit(request).intercept_response,true);
drafts.get('a').raw='POST /changed HTTP/1.1\n\na\nb';assert.match(c.pendingEdit(request).request,/changed/);
const response={id:'b',stage:'response',response:'HTTP/1.1 200 OK\r\n\r\nopaque\ufffd'};drafts.set('b',{raw:'HTTP/1.1 200 OK\n\nopaque\ufffd'});assert.equal(c.pendingEdit(response).response,undefined,'untouched binary display must not replace original bytes');drafts.get('b').raw='HTTP/1.1 202 Accepted\n\nchanged';assert.match(c.pendingEdit(response).response,/202 Accepted/);
console.log('5 interception UI payload checks passed');
