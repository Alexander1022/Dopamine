/* Parse a copied cURL command as data. No process or shell is invoked. */
(function(root){
function tokenizeCurl(source){
 const out=[];let word='',active=false,i=0;
 const escaped=()=>{if(i>=source.length)throw Error('Incomplete escape.');const c=source[i++];if(c==='\n')return '';if(c==='\r'&&source[i]==='\n'){i++;return ''}return c};
 while(i<source.length){let c=source[i++];
  if(/\s/.test(c)){if(active){out.push(word);word='';active=false}continue}
  if(c==='\\'){const part=escaped();if(part){active=true;word+=part}continue}
  active=true;
  const ansi=c==='$'&&source[i]==="'";
  if(c==="'"||c==='"'||ansi){if(ansi){i++;c="'"}let closed=false;while(i<source.length){let d=source[i++];if(d===c){closed=true;break}if(d==='\\'&&(c==='"'||ansi)){if(i>=source.length)throw Error('Incomplete quoted escape.');const e=source[i++];if(e==='\n')continue;if(ansi){const map={n:'\n',r:'\r',t:'\t',b:'\b',f:'\f',v:'\v',a:'\x07',"'":"'",'"':'"','\\':'\\'};if(e==='x'){const m=source.slice(i).match(/^[0-9a-f]{1,2}/i);if(!m)throw Error('Invalid hex escape.');word+=String.fromCharCode(parseInt(m[0],16));i+=m[0].length}else if(/[0-7]/.test(e)){const m=(e+source.slice(i)).match(/^[0-7]{1,3}/)[0];word+=String.fromCharCode(parseInt(m,8));i+=m.length-1}else word+=map[e]??'\\'+e}else word+=['$','`','"','\\'].includes(e)?e:'\\'+e;}else word+=d}if(!closed)throw Error('Unclosed quote in cURL command.');continue}
  if(/[|;&<>`]/.test(c))throw Error('Paste one cURL command without shell operators.');
  word+=c;
 }
 if(active)out.push(word);return out;
}
function parseCurl(source){
 const args=tokenizeCurl(source.trim());if(!/^(?:curl|curl\.exe)$/i.test(args.shift()||''))throw Error('The request must start with curl. Choose Copy as cURL (bash) in your browser.');
 let url='',method='',head=false,get=false,headers=[],data=[],dataType='',auth='',cookies=[];const notes=[];
 const value=(inline,index)=>{if(inline!==undefined)return[inline,index];if(index+1>=args.length)throw Error('Missing value for '+args[index]);return[args[index+1],index+1]};
 for(let i=0;i<args.length;i++){
  let arg=args[i],flag=arg,inline;const equal=arg.startsWith('--')?arg.indexOf('='):-1;
  if(equal>0){flag=arg.slice(0,equal);inline=arg.slice(equal+1)}
  else if(/^-[XHbudAe]/.test(arg)&&arg.length>2){flag=arg.slice(0,2);inline=arg.slice(2)}
  if(['--compressed','--globoff','-g','--silent','-s','--show-error','-S','--http1.1','--http2'].includes(flag)){if(flag==='--compressed'||flag==='--http2')notes.push(flag+' is a transport option; Dopamine sends HTTP/1.1.');continue}
  if(flag==='-I'||flag==='--head'){head=true;continue}if(flag==='-G'||flag==='--get'){get=true;continue}
  const values=['--url','-X','--request','-H','--header','-b','--cookie','-d','--data','--data-raw','--data-binary','--data-urlencode','--json','-u','--user','-A','--user-agent','-e','--referer'];
  if(values.includes(flag)){
   let v;[v,i]=value(inline,i);
   if(flag==='--url')url=v;
   else if(['-X','--request'].includes(flag))method=v;
   else if(['-H','--header'].includes(flag)){const n=v.indexOf(':');if(n<1)throw Error('Header must be Name: value.');headers.push([v.slice(0,n).trim(),v.slice(n+1).trim()])}
   else if(['-b','--cookie'].includes(flag)){if(!v.includes('='))throw Error('Cookie files are not supported; paste cookie values.');cookies.push(v)}
   else if(['-u','--user'].includes(flag))auth=v;
   else if(['-A','--user-agent'].includes(flag))headers.push(['User-Agent',v]);
   else if(['-e','--referer'].includes(flag))headers.push(['Referer',v]);
   else {if(v.startsWith('@')&&flag!=='--data-raw')throw Error('File uploads are not imported. Paste an inline request body.');if(flag==='--data-urlencode'){const n=v.indexOf('=');v=n>=0?v.slice(0,n+1)+encodeURIComponent(v.slice(n+1)):encodeURIComponent(v)}data.push(v);dataType=flag==='--json'?'application/json':'application/x-www-form-urlencoded'}
  }else if(flag.startsWith('-'))throw Error('Unsupported cURL option: '+flag+'. Remove it or use Copy as cURL (bash).');
  else {if(url)throw Error('Import one URL at a time.');url=arg}
 }
 if(!url)throw Error('No URL found in the cURL command.');if(!/^https?:\/\//i.test(url))url='http://'+url;
 const u=new URL(url);if(!['http:','https:'].includes(u.protocol))throw Error('Use an HTTP or HTTPS URL.');
 if(u.username||u.password)throw Error('Use --user for credentials rather than credentials inside the URL.');u.hash='';
 let body=data.join(dataType==='application/json'?'':'&');if(get&&data.length){u.search+=(u.search?'&':'?')+body;body=''}
 method=method||(head?'HEAD':get?'GET':data.length?'POST':'GET');if(!/^[A-Za-z]+$/.test(method))throw Error('Invalid request method.');
 const has=n=>headers.some(([k])=>k.toLowerCase()===n);
 if(!has('host'))headers.unshift(['Host',u.host]);if(cookies.length)headers.push(['Cookie',cookies.join('; ')]);
 if(auth&&!has('authorization')){const bytes=new TextEncoder().encode(auth.includes(':')?auth:auth+':');headers.push(['Authorization','Basic '+btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''))])}
 if(data.length&&!get&&!has('content-type'))headers.push(['Content-Type',dataType]);if(dataType==='application/json'&&!has('accept'))headers.push(['Accept','application/json']);
 headers=headers.filter(([k])=>!['content-length','connection','accept-encoding'].includes(k.toLowerCase()));
 for(const [k,v]of headers)if(!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(k)||/[\r\n\0]/.test(v))throw Error('Invalid header name or value.');
 if(data.length&&!get)headers.push(['Content-Length',String(new TextEncoder().encode(body).length)]);
 if(args.some(a=>/^accept-encoding:/i.test(a)))notes.push('Accept-Encoding removed so the response remains readable.');
 return{url:u.href,request:method.toUpperCase()+' '+u.pathname+u.search+' HTTP/1.1\r\n'+headers.map(([k,v])=>k+': '+v+'\r\n').join('')+'\r\n'+body,notes:[...new Set(notes)]};
}
root.parseCurl=parseCurl;if(typeof module!=='undefined')module.exports={parseCurl,tokenizeCurl};
})(typeof globalThis!=='undefined'?globalThis:window);
