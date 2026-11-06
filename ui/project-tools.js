/* Project tools: the project name, message search and In scope only in HTTP history, the Scope and Match and replace
   views, the right-click menu on history and site map, original/modified views of rewritten exchanges, the
   Accept-Encoding setting and Open Browser. Scope, rules and tabs are saved in the open project by the engine. */
(() => {
  const button = (label, onclick, cls = '') => { const b = document.createElement('button'); b.textContent = label; b.onclick = onclick; if (cls) b.className = cls; return b; };
  const TARGETS = {request_line: 'Request line', request_header: 'Request header', request_body: 'Request body',
                   response_line: 'Response status line', response_header: 'Response header', response_body: 'Response body'};
  const COLORS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple', 'gray'];

  // Project name in the header: Dopamine / Project / View.
  const crumb = document.createElement('span');
  crumb.id = 'projectName';
  crumb.className = 'project-name';
  const sep = document.createElement('span');
  sep.className = 'crumb-sep'; sep.setAttribute('aria-hidden', 'true'); sep.textContent = '/';
  $('viewTitle').before(crumb, sep);
  function renderProject() {
    const project = status.project || {};
    crumb.textContent = project.name || '';
    crumb.title = project.path ? project.path + ' · File → Open Project to switch' : '';
  }

  // HTTP history: message search, In scope only, Load older, Open Browser.
  const search = document.createElement('div');
  search.className = 'history-search';
  search.innerHTML = '<label class="sr-only" for="contentSearch">Search messages</label><input id="contentSearch" type="search" placeholder="Search requests and responses" spellcheck="false">' +
    '<button id="searchCase" class="toggle" aria-pressed="false" title="Match case">Aa</button><button id="searchRegex" class="toggle" aria-pressed="false" title="Regular expression">.*</button>' +
    '<select id="searchIn" aria-label="Search in"><option value="both">Both</option><option value="request">Requests</option><option value="response">Responses</option></select>' +
    '<label class="tiny scope-only"><input id="scopeOnly" type="checkbox"> In scope only</label>';
  $('filter').after(search);
  $('filter').placeholder = 'Filter by URL, method or status';
  // Open Browser sits at the end of the Proxy tab bar, so it is in reach from every Proxy view.
  const browserButton = button('', openBrowser, 'subnav-action');
  browserButton.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg><span>Open Browser</span>';
  browserButton.title = 'Open Chrome, Brave, Edge or Chromium with a fresh profile that sends its traffic, localhost included, through this proxy (⇧⌘B)';
  document.querySelector('#proxy .subnav').append(browserButton);
  const more = document.createElement('div');
  more.className = 'load-more';
  more.hidden = true;
  more.append(button('Load older requests', () => { historyLimit += 500; refresh(); }));
  $('historyEmpty').after(more);
  let scopeTouched = false, searchTimer;
  function applySearch() {
    window.historySearch = {search: $('contentSearch').value, in: $('searchIn').value, regex: $('searchRegex').getAttribute('aria-pressed') === 'true',
                            case: $('searchCase').getAttribute('aria-pressed') === 'true', scope: $('scopeOnly').checked};
    historyLimit = 500;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refresh, 200);
  }
  for (const id of ['searchCase', 'searchRegex']) $(id).onclick = () => { $(id).setAttribute('aria-pressed', String($(id).getAttribute('aria-pressed') !== 'true')); applySearch(); };
  $('contentSearch').oninput = applySearch;
  $('searchIn').onchange = applySearch;
  $('scopeOnly').onchange = () => { scopeTouched = true; applySearch(); };
  $('filter').addEventListener('input', () => { historyLimit = 500; clearTimeout(searchTimer); searchTimer = setTimeout(refresh, 200); });
  $('sourceFilter').addEventListener('change', () => { historyLimit = 500; refresh(); });
  function syncScopeToggle() {
    // In scope only follows the project's scope until you change it yourself.
    if (!scopeTouched && $('scopeOnly').checked !== !!status.scope_active) { $('scopeOnly').checked = !!status.scope_active; applySearch(); }
    $('scopeOnly').disabled = !status.scope_active && !$('scopeOnly').checked;
    more.hidden = !historyMore;
  }

  function openBrowser() {
    try { window.webkit.messageHandlers.openBrowser.postMessage(''); notify('Opening a browser that uses the proxy…'); }
    catch { notify('Open Browser is available in the Dopamine app. Point a browser at 127.0.0.1:' + (status.proxy_port || 18080) + ' instead.', true); }
  }

  // Selected exchange: jump to the search match, and switch between the modified and original messages.
  const version = document.createElement('select');
  version.id = 'historyVersion';
  version.setAttribute('aria-label', 'Message version');
  version.add(new Option('As sent', 'modified'));
  version.add(new Option('Before rules', 'original'));
  version.hidden = true;
  version.title = 'A match-and-replace rule changed this exchange';
  $('historyToRepeater').before(version);
  function showVersion() {
    if (!selected) return;
    const original = version.value === 'original';
    setMessage('historyRequest', (original && selected.original_request) || selected.request || '');
    setMessage('historyResponse', (original && selected.original_response) || selected.response || '');
  }
  version.onchange = showVersion;
  function findMatch() {
    const s = window.historySearch || {};
    if (!s.search) return;
    for (const id of s.in === 'request' ? ['historyRequest'] : s.in === 'response' ? ['historyResponse'] : ['historyResponse', 'historyRequest']) {
      const el = $(id), text = el.value;
      let start = -1, length = 0;
      if (s.regex) {
        try { const m = new RegExp(s.search, s.case ? '' : 'i').exec(text); if (m) { start = m.index; length = m[0].length; } } catch {}
      } else {
        start = s.case ? text.indexOf(s.search) : text.toLowerCase().indexOf(s.search.toLowerCase());
        length = s.search.length;
      }
      if (start >= 0) {
        const find = el.closest('.pane').querySelector('.message-tools input[type=search]');
        if (find && !s.regex) find.value = s.search;
        el.focus(); el.setSelectionRange(start, start + length);
        return;
      }
    }
  }
  const baseSelect = selectHistory;
  selectHistory = async function (id) {
    await baseSelect(id);
    if (selected?.id !== id) return;
    version.hidden = !selected.rewritten;
    version.value = 'modified';
    findMatch();
  };

  // Scope and Match and replace views under Proxy.
  const subnav = document.querySelector('#proxy .subnav');
  for (const [name, label] of [['scope', 'Scope'], ['rules', 'Match and replace']]) {
    const b = document.createElement('button');
    b.dataset.sub = name; b.setAttribute('aria-selected', 'false'); b.textContent = label;
    b.onclick = () => { chooseSub(name); loadWorkspace(); };
    subnav.querySelector('[data-sub="settings"]').before(b);
  }
  subTitles.scope = 'Scope';
  subTitles.rules = 'Match and replace';
  const scopeView = document.createElement('div');
  scopeView.id = 'scope'; scopeView.className = 'subview';
  scopeView.innerHTML = `<div class="settings"><section class="setting-row"><div class="setting-label"><h2>Target scope</h2><p>Hosts you are testing in this project. In-scope traffic is what Intercept holds, what rules change by default, and what agents see first. With no included hosts, everything is in scope.</p><p>Right-click a request in HTTP history or a host in the site map to add it.</p></div>
    <div class="setting-panel"><form id="scopeAdd" class="toolbar scope-add"><select id="scopeKind" aria-label="Include or exclude"><option value="include">Include</option><option value="exclude">Exclude</option></select>
    <input id="scopeHost" placeholder="*.example.com" spellcheck="false" aria-label="Host pattern" required><input id="scopePath" placeholder="/path prefix (optional)" spellcheck="false" aria-label="Path prefix"><button class="primary">Add</button></form>
    <div class="table-wrap scope-table"><table aria-label="Scope"><thead><tr><th>Rule</th><th>Host</th><th>Path prefix</th><th></th></tr></thead><tbody id="scopeRows"></tbody></table><p id="scopeEmpty" class="empty">Everything is in scope. Add a host to focus Intercept, rules and agents on it.</p></div>
    <p class="tls-note"><code class="inline">*.example.com</code> also matches example.com. Exclude wins over include.</p>
    <div class="toolbar"><label><input id="scopeRecord" type="checkbox"> Record out-of-scope traffic in history</label></div>
    <p class="tls-note">Out-of-scope traffic always passes through the proxy. Recording it lets you look back later; turn off In scope only in HTTP history to see it.</p></div></section></div>`;
  const rulesView = document.createElement('div');
  rulesView.id = 'rules'; rulesView.className = 'subview';
  rulesView.innerHTML = `<div class="settings"><section class="setting-row"><div class="setting-label"><h2>Match and replace</h2><p>Rewrite proxy traffic automatically, in order, before Intercept. Rules don't touch Repeater or agent requests.</p><p>History keeps what was actually sent and received; choose <b>Before rules</b> on a rewritten request to see the original.</p></div>
    <div class="setting-panel"><div class="table-wrap rules-table"><table aria-label="Rules"><thead><tr><th>On</th><th>Applies to</th><th>Match</th><th>Replace</th><th>Traffic</th><th></th></tr></thead><tbody id="ruleRows"></tbody></table><p id="rulesEmpty" class="empty">No rules yet.</p></div>
    <form id="ruleForm" class="rule-form"><h3 id="ruleFormTitle">New rule</h3>
    <label>Applies to <select id="ruleTarget">${Object.entries(TARGETS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
    <label>Match <input id="ruleMatch" spellcheck="false" placeholder="Text to find, or empty to add a header"></label>
    <label>Replace <input id="ruleReplace" spellcheck="false" placeholder="Replacement; empty removes a header"></label>
    <label>Comment <input id="ruleComment" spellcheck="false" placeholder="What this rule is for"></label>
    <div class="toolbar"><label><input id="ruleRegex" type="checkbox"> Regular expression ($1 inserts a group)</label><label><input id="ruleAll" type="checkbox"> Also out-of-scope traffic</label><span class="grow"></span><button type="button" id="ruleCancel" hidden>Cancel</button><button class="primary" id="ruleSave">Add rule</button></div>
    <p id="ruleError" class="error" role="alert"></p></form></div></section></div>`;
  document.querySelector('#proxy #settings').before(scopeView, rulesView);

  let scope = null, rules = [], workspaceRevision = -1, editing = null;
  async function loadWorkspace(force = false) {
    if (view !== 'proxy' || !['scope', 'rules'].includes(sub)) return;
    const revision = status.project?.revision;
    if (!force && revision === workspaceRevision) return;
    try {
      [scope, rules] = await Promise.all([api('scope'), api('rules').then(r => r.rules)]);
      workspaceRevision = revision;
      renderScope(); renderRules();
    } catch (e) { notify(e.message, true); }
  }
  const agentMark = () => { const s = document.createElement('span'); s.className = 'agent-mark'; s.textContent = 'Agent'; s.title = 'Added by an agent'; return s; };
  function renderScope() {
    if (!scope) return;
    $('scopeRows').replaceChildren();
    for (const kind of ['include', 'exclude']) {
      scope[kind].forEach((entry, index) => {
        const tr = document.createElement('tr');
        textCell(tr, kind === 'include' ? 'Include' : 'Exclude', kind === 'exclude' ? 'error' : '');
        textCell(tr, entry.host, 'host');
        textCell(tr, entry.path || 'Any path', entry.path ? 'path' : 'muted');
        const actions = document.createElement('td');
        actions.className = 'num';
        if (entry.agent) actions.append(agentMark());
        actions.append(button('Remove', () => saveScope({[kind]: scope[kind].filter((_, i) => i !== index)})));
        tr.append(actions);
        $('scopeRows').append(tr);
      });
    }
    $('scopeEmpty').hidden = !!(scope.include.length || scope.exclude.length);
    $('scopeRecord').checked = scope.record_out_of_scope;
  }
  async function saveScope(changes) {
    try { scope = await api('scope', changes); renderScope(); await refresh(); workspaceRevision = status.project?.revision; notify('Scope saved'); }
    catch (e) { notify(e.message, true); }
  }
  async function addScope(host, path, exclude) {
    try { scope = await api('scope', {add: {host, path, exclude}}); renderScope(); await refresh(); notify((exclude ? 'Excluded ' : 'Added to scope: ') + host + (path || '')); }
    catch (e) { notify(e.message, true); }
  }
  $('scopeAdd').onsubmit = e => { e.preventDefault(); addScope($('scopeHost').value.trim(), $('scopePath').value.trim(), $('scopeKind').value === 'exclude').then(() => { $('scopeHost').value = ''; $('scopePath').value = ''; }); };
  $('scopeRecord').onchange = () => saveScope({record_out_of_scope: $('scopeRecord').checked});

  function renderRules() {
    $('ruleRows').replaceChildren();
    rules.forEach((rule, index) => {
      const tr = document.createElement('tr');
      tr.classList.toggle('rule-off', !rule.enabled);
      const on = document.createElement('td'), box = document.createElement('input');
      box.type = 'checkbox'; box.checked = rule.enabled; box.setAttribute('aria-label', 'Rule enabled');
      box.onchange = () => saveRules(rules.map((r, i) => i === index ? {...r, enabled: box.checked} : r));
      on.append(box); tr.append(on);
      textCell(tr, TARGETS[rule.target] + (rule.regex ? ' · regex' : ''));
      textCell(tr, rule.match || '(add header)', 'path');
      textCell(tr, rule.replace || '(remove)', 'path');
      textCell(tr, rule.all_traffic ? 'All' : 'In scope');
      const actions = document.createElement('td');
      actions.className = 'num rule-actions';
      if (rule.agent) actions.append(agentMark());
      if (rule.comment) tr.title = rule.comment;
      const up = button('↑', () => saveRules(swap(index, -1))), down = button('↓', () => saveRules(swap(index, 1)));
      up.disabled = index === 0; down.disabled = index === rules.length - 1;
      up.setAttribute('aria-label', 'Move up'); down.setAttribute('aria-label', 'Move down');
      actions.append(up, down, button('Edit', () => editRule(rule)), button('Delete', () => saveRules(rules.filter(r => r !== rule))));
      tr.append(actions);
      $('ruleRows').append(tr);
    });
    $('rulesEmpty').hidden = !!rules.length;
  }
  function swap(index, delta) { const list = [...rules], j = index + delta; [list[index], list[j]] = [list[j], list[index]]; return list; }
  async function saveRules(list) {
    try { rules = (await api('rules', {rules: list})).rules; renderRules(); await refresh(); workspaceRevision = status.project?.revision; notify('Rules saved'); return true; }
    catch (e) { $('ruleError').textContent = e.message; notify(e.message, true); return false; }
  }
  function editRule(rule) {
    editing = rule;
    $('ruleFormTitle').textContent = rule ? 'Edit rule' : 'New rule';
    $('ruleSave').textContent = rule ? 'Save rule' : 'Add rule';
    $('ruleCancel').hidden = !rule;
    $('ruleTarget').value = rule?.target || 'request_header';
    $('ruleMatch').value = rule?.match || '';
    $('ruleReplace').value = rule?.replace || '';
    $('ruleComment').value = rule?.comment || '';
    $('ruleRegex').checked = !!rule?.regex;
    $('ruleAll').checked = !!rule?.all_traffic;
    $('ruleError').textContent = '';
    if (rule) $('ruleMatch').focus();
  }
  $('ruleCancel').onclick = () => editRule(null);
  $('ruleForm').onsubmit = async e => {
    e.preventDefault();
    const fields = {target: $('ruleTarget').value, match: $('ruleMatch').value, replace: $('ruleReplace').value, comment: $('ruleComment').value,
                    regex: $('ruleRegex').checked, all_traffic: $('ruleAll').checked};
    const list = editing ? rules.map(r => r === editing ? {...r, ...fields} : r) : [...rules, {...fields, enabled: true}];
    if (await saveRules(list)) editRule(null);
  };

  // Right-click on history rows and site map hosts.
  const menu = document.createElement('div');
  menu.className = 'context-menu'; menu.hidden = true; menu.setAttribute('role', 'menu');
  document.body.append(menu);
  const noteDialog = document.createElement('dialog');
  noteDialog.innerHTML = '<form method="dialog"><div class="pane-head"><h2>Note</h2></div><p>Shown on this request in HTTP history.</p><textarea id="noteText" class="setting-text" maxlength="4000" aria-label="Note"></textarea><div class="toolbar"><span class="grow"></span><button value="cancel">Cancel</button><button value="save" class="primary">Save note</button></div></form>';
  document.body.append(noteDialog);
  function closeMenu() { menu.hidden = true; }
  function showMenu(x, y, items) {
    menu.replaceChildren();
    for (const item of items) {
      if (item === '-') { const hr = document.createElement('hr'); menu.append(hr); continue; }
      const b = button(item.label, () => { closeMenu(); item.run(); });
      b.setAttribute('role', 'menuitem');
      if (item.cls) b.className = item.cls;
      menu.append(b);
    }
    menu.hidden = false;
    const rect = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, innerWidth - rect.width - 8) + 'px';
    menu.style.top = Math.min(y, innerHeight - rect.height - 8) + 'px';
    menu.querySelector('button')?.focus();
  }
  document.addEventListener('click', e => { if (!menu.contains(e.target)) closeMenu(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  function scopeItems(url) {
    let u; try { u = new URL(url); } catch { return []; }
    const dir = u.pathname.replace(/[^/]*$/, '') || '/';
    return [{label: 'Add ' + u.hostname + ' to scope', run: () => addScope(u.hostname, '', false)},
            ...(dir !== '/' ? [{label: 'Add ' + u.hostname + dir + ' to scope', run: () => addScope(u.hostname, dir, false)}] : []),
            {label: 'Exclude ' + u.hostname + ' from scope', run: () => addScope(u.hostname, '', true)}];
  }
  async function annotate(id, changes) {
    try { await api('history/' + id + '/annotate', changes); await refresh(); if (selected?.id === id) await selectHistory(id); }
    catch (e) { notify(e.message, true); }
  }
  $('historyRows').addEventListener('contextmenu', e => {
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    e.preventDefault();
    const id = Number(tr.dataset.id), row = history.find(r => r.id === id);
    if (!row) return;
    showMenu(e.clientX, e.clientY, [...scopeItems(row.url), '-',
      ...COLORS.map(c => ({label: 'Highlight ' + c, cls: 'swatch mark-' + c, run: () => annotate(id, {color: c})})),
      ...(row.color ? [{label: 'Remove highlight', run: () => annotate(id, {color: ''})}] : []), '-',
      {label: row.note ? 'Edit note…' : 'Add note…', run: () => { $('noteText').value = row.note || ''; noteDialog.returnValue = ''; noteDialog.onclose = () => { if (noteDialog.returnValue === 'save') annotate(id, {note: $('noteText').value.trim()}); }; noteDialog.showModal(); }},
      ...(row.tunnel ? [] : [{label: 'Send to Repeater', run: async () => { const item = await api('history/' + id); addTab(item.url, item.request); }}])]);
  });
  document.addEventListener('contextmenu', e => {
    const node = e.target.closest('#targetTree button');
    if (!node) return;
    e.preventDefault();
    showMenu(e.clientX, e.clientY, scopeItems(node.title));
  });

  // Proxy settings: Accept-Encoding.
  const facts = document.querySelector('#settings .facts');
  const traffic = document.createElement('div');
  traffic.className = 'toolbar traffic-setting';
  traffic.innerHTML = '<label><input id="stripEncoding" type="checkbox"> Ask servers for uncompressed responses</label>';
  const trafficNote = document.createElement('p');
  trafficNote.className = 'tls-note';
  trafficNote.textContent = 'Removes Accept-Encoding from proxied requests so match-and-replace and search see plain bodies. gzip and deflate responses are decoded either way; Brotli is shown as received.';
  facts.after(traffic, trafficNote);
  api('traffic').then(t => { $('stripEncoding').checked = t.strip_accept_encoding; }).catch(() => {});
  $('stripEncoding').onchange = async () => {
    try { await api('traffic', {strip_accept_encoding: $('stripEncoding').checked}); notify('Traffic setting saved'); }
    catch (e) { notify(e.message, true); }
  };

  // Native menu routes and keeping everything current.
  const nativeGo = window.dopamineNative.go;
  window.dopamineNative.go = name => { if (['scope', 'rules'].includes(name)) { chooseView('proxy'); chooseSub(name); loadWorkspace(true); } else nativeGo(name); };
  window.dopamineNative.openBrowser = openBrowser;
  document.addEventListener('dopamine:project', () => { workspaceRevision = -1; scopeTouched = false; editRule(null); renderProject(); loadWorkspace(true); });
  setInterval(() => { renderProject(); syncScopeToggle(); loadWorkspace(); }, 700);
  if (['scope', 'rules'].includes(location.hash.slice(1))) window.dopamineNative.go(location.hash.slice(1));
})();
