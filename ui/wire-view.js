/* Wire view: draws the grammar of each HTTP message (request or status line, headers, JSON/HTML body, hex rows)
   behind its textarea. The textarea stays the editing, selection and find surface; only its glyphs are hidden. */
(() => {
  const LIMIT = 400000;
  const ids = ['historyRequest', 'historyResponse', 'repeaterRequest', 'repeaterResponse', 'pendingRequest', 'targetRequest', 'targetResponse'];
  const valueProp = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  const esc = s => s.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
  const tok = (cls, s) => s ? `<span class="w-${cls}">${esc(s)}</span>` : '';
  const statusClass = code => code >= 500 ? 's5' : code >= 400 ? 's4' : code >= 300 ? 's3' : 's2';

  function json(text) {
    return esc(text).replace(/("(?:\\.|[^"\\\n])*")(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([{}[\],])/g,
      (all, str, colon, lit, num, punct) => str ? (colon ? `<span class="w-key">${str}</span>${colon}` : `<span class="w-string">${str}</span>`)
        : lit || num ? `<span class="w-number">${all}</span>` : `<span class="w-punct">${punct}</span>`);
  }
  function markup(text) {
    return esc(text).replace(/(&lt;\/?)([\w:-]+)([^]{0,2000}?)(\/?&gt;)/g, (all, open, name, attrs, close) =>
      `<span class="w-punct">${open}</span><span class="w-tag">${name}</span>` +
      attrs.replace(/([\w:-]+)(=)("[^"]*"|'[^']*')/g, '<span class="w-attr">$1</span><span class="w-punct">$2</span><span class="w-string">$3</span>') +
      `<span class="w-punct">${close}</span>`);
  }
  function body(text, type) {
    const start = text.trimStart()[0];
    if (/json/i.test(type) || start === '{' || start === '[') return json(text);
    if (/html|xml/i.test(type) || start === '<') return markup(text);
    return esc(text);
  }
  function hex(text) {
    return text.split('\n').map(line => {
      const m = /^([0-9a-f]{8})(  )(.{0,47})(  )?(.*)$/.exec(line);
      return m ? tok('offset', m[1]) + m[2] + esc(m[3]) + (m[4] || '') + tok('ascii', m[5]) : esc(line);
    }).join('\n');
  }
  function highlight(text) {
    if (!text) return '';
    if (text.length > LIMIT) return esc(text);
    if (/^[0-9a-f]{8}  /.test(text)) return hex(text);
    const gap = /\r?\n\r?\n/.exec(text);
    const head = gap ? text.slice(0, gap.index) : text, rest = gap ? text.slice(gap.index + gap[0].length) : '';
    const lines = head.split(/(\r?\n)/), first = lines[0];
    let out;
    const response = /^(HTTP\/[\d.]+)( +)(\d{3})(.*)$/.exec(first), request = /^([A-Z]+)( +)(\S+)( +)(HTTP\/[\d.]+)\s*$/.exec(first);
    if (response) out = tok('version', response[1]) + response[2] + tok(statusClass(+response[3]), response[3]) + esc(response[4]);
    else if (request) out = tok('method', request[1]) + request[2] + esc(request[3]) + request[4] + tok('version', request[5]);
    else return body(text, '');
    let type = '';
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];
      if (i % 2) { out += line; continue; }
      const h = /^([^:\s][^:]*)(:)(.*)$/.exec(line);
      if (!h) { out += esc(line); continue; }
      if (/^content-type$/i.test(h[1])) type = h[3];
      out += tok('name', h[1]) + tok('punct', h[2]) + esc(h[3]);
    }
    return gap ? out + gap[0] + body(rest, type) : out;
  }

  function mount(el) {
    if (!el || el.dataset.wire) return;
    el.dataset.wire = 'on';
    const box = document.createElement('div'), pre = document.createElement('pre');
    box.className = 'wire';
    pre.className = 'code wire-hl';
    pre.setAttribute('aria-hidden', 'true');
    el.before(box);
    box.append(pre, el);
    let queued = false;
    const fit = () => {
      pre.style.width = el.clientWidth + 'px';
      pre.style.height = el.clientHeight + 'px';
      pre.scrollTop = el.scrollTop;
      pre.scrollLeft = el.scrollLeft;
    };
    const draw = () => {
      queued = false;
      pre.innerHTML = highlight(valueProp.get.call(el)) + '\n ';
      fit();
    };
    const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(draw); } };
    Object.defineProperty(el, 'value', {configurable: true, get() { return valueProp.get.call(this); }, set(v) { valueProp.set.call(this, v); schedule(); }});
    el.addEventListener('input', schedule);
    el.addEventListener('scroll', fit, {passive: true});
    new MutationObserver(() => { pre.style.whiteSpace = el.style.whiteSpace; schedule(); }).observe(el, {attributes: true, attributeFilter: ['style']});
    new ResizeObserver(fit).observe(el);
    draw();
  }

  ids.forEach(id => mount(document.getElementById(id)));
  window.wireHighlight = highlight;
})();
