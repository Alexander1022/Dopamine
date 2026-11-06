/* Built-in MCP connection and permissions panel. Uses the existing local UI API. */
(() => {
  const nav = document.querySelector('.topnav');
  const button = document.createElement('button');
  button.dataset.view = 'agents';
  button.setAttribute('aria-selected', 'false');
  button.innerHTML = '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0V8Zm6 9v4"/></svg><span class="nav-label">Agents</span>';
  button.title = 'Agents';
  nav.append(button);
  const panel = document.createElement('section');
  panel.id = 'agents';
  panel.className = 'view';
  panel.setAttribute('aria-label', 'Agents');
  panel.innerHTML = `
    <div class="toolbar"><strong>Agent access</strong><span class="grow"></span><span id="mcpState" role="status">Connecting…</span><button id="mcpToggle">Disable access</button></div>
    <div class="agent-scroll"><div class="agent-intro"><h2>Connect Dopamine to your agent</h2><p>Let Codex, Claude and other MCP clients inspect traffic and work with HTTP messages. Keep Dopamine open while your agent is connected.</p></div>
    <div class="agent-columns"><section class="agent-setup" aria-labelledby="mcpConnectHeading"><h3 id="mcpConnectHeading">Connect a client</h3>
    <label for="mcpClient">Client configuration</label><select id="mcpClient"><option value="codex_cli">Codex CLI</option><option value="claude_code">Claude Code</option><option value="claude">Claude Desktop · JSON</option><option value="codex">Codex · config.toml</option></select>
    <p id="mcpConfigHelp">Run this command in a terminal, then reconnect your MCP client.</p><pre id="mcpConfig" tabindex="0" aria-label="MCP client configuration"></pre><button id="mcpCopy">Copy configuration</button>
    <details class="agent-transport"><summary>Connection details</summary><dl><dt>Transport</dt><dd>stdio bridge → local Streamable HTTP</dd><dt>Endpoint</dt><dd id="mcpEndpoint"></dd><dt>Local token file</dt><dd id="mcpTokenFile"></dd></dl><p>The bridge reads the token automatically. No token is embedded in the client configuration.</p></details></section>
    <section class="agent-permissions" aria-labelledby="mcpPermissionsHeading"><h3 id="mcpPermissionsHeading">Permissions</h3><label for="mcpPermission">Agent permissions</label><select id="mcpPermission"><option value="read">Read</option><option value="workspace">Workspace</option><option value="active">Active</option><option value="everything">Everything</option></select><p id="mcpPermissionHelp"></p>
    <h3>Available tools <span id="mcpToolCount" class="muted"></span></h3><ul class="agent-tools"><li><strong>Read</strong><span>Search history and message contents, read exchanges, scope, rules, Repeater tabs and held messages. Decode text.</span></li><li><strong>Workspace</strong><span>Edit scope, draft match-and-replace rules (they start off), stage Repeater tabs, highlight and annotate history.</span></li><li><strong>Active</strong><span>Send requests and Repeater tabs, forward or drop held messages, start or stop the proxy and Intercept.</span></li><li><strong>Everything</strong><span>All tools without guardrails: agents can switch rules on and send to any host without confirming.</span></li></ul><p class="agent-limit">Agents can't switch projects or send requests to Dopamine itself. Below Everything, sending to hosts outside the project scope needs the agent to confirm it. Unsent desktop editor drafts stay in the editor.</p></section></div>
    <section class="agent-activity" aria-labelledby="mcpActivityHeading"><div class="agent-section-head"><h3 id="mcpActivityHeading">Agent history</h3><span id="mcpHistoryCount" class="muted"></span></div><p class="muted">Every tool call in this project: what the agent did, the endpoints it touched and the outcome. Bodies, header values and credentials are left out. Select a row for its arguments.</p>
    <div class="agent-history-tools"><input type="search" id="mcpHistorySearch" placeholder="Filter by tool, action, URL or error" aria-label="Filter agent history"><select id="mcpHistoryKind" aria-label="Kind of call"><option value="">All calls</option><option value="read">Reads</option><option value="change">Changes</option><option value="send">Sends</option><option value="failed">Failures</option></select><select id="mcpHistoryClient" aria-label="Client"><option value="">All clients</option></select><span class="grow"></span><button id="mcpHistoryClear">Clear history</button></div>
    <div class="table-wrap"><table aria-label="Agent history"><thead><tr><th class="num">Time</th><th>Client</th><th>Action</th><th>Endpoint</th><th>Result</th><th class="num">Duration</th></tr></thead><tbody id="mcpActivity"></tbody></table></div><p id="mcpActivityEmpty">No agent tools have been called in this project.</p><button id="mcpHistoryMore" hidden>Load older calls</button></section></div>`;
  document.querySelector('main').append(panel);
  let settings, fetching = false, saving = false, historyKey = '', historyItems = [], historyMore = null, historyTotal = 0, clearArmed = null;
  const expanded = new Set();
  const tierNames = {read: 'Read', workspace: 'Workspace', active: 'Active', everything: 'Everything'};
  const tierHelp = {
    read: 'Agents can look at traffic, search, and read scope, rules, Repeater tabs and held messages. Nothing changes.',
    workspace: 'Agents can also organize the project: edit scope, draft rules you turn on yourself, stage Repeater tabs and annotate history. Nothing is sent to targets.',
    active: 'Agents can also send traffic to targets, forward or drop held messages, and control the proxy and Intercept.',
    everything: 'Agents can use every tool without guardrails: they can switch match-and-replace rules on, their edits keep rules on, and they send to hosts outside the scope without confirming. Use it only with agents and targets you trust.'
  };
  const helpers = {
    codex_cli: 'Run this command in a terminal, then reconnect Codex. Existing client settings are preserved.',
    claude_code: 'Run this command in a terminal from the project where you want to use Dopamine, then reconnect Claude Code.',
    claude: 'Merge this entry into your Claude Desktop MCP configuration, then restart Claude Desktop.',
    codex: 'Add this section to your Codex config.toml, then reconnect Codex.'
  };
  function showConfig() {
    if (!settings) return;
    $('mcpConfig').textContent = settings.configs[$('mcpClient').value];
    $('mcpConfigHelp').textContent = helpers[$('mcpClient').value];
  }
  function render() {
    if (!settings) return;
    $('mcpState').textContent = !settings.enabled ? 'Access disabled' : tierNames[settings.access] + ' · Local';
    $('mcpToggle').textContent = settings.enabled ? 'Disable access' : 'Enable access';
    $('mcpPermission').value = settings.access;
    $('mcpPermissionHelp').textContent = tierHelp[settings.access];
    $('mcpEndpoint').textContent = settings.endpoint;
    $('mcpTokenFile').textContent = settings.token_file;
    $('mcpToolCount').textContent = '· ' + settings.tool_count;
    showConfig();
  }
  function historyParams(extra = {}) {
    const p = new URLSearchParams({limit: 50, ...extra}), q = $('mcpHistorySearch').value.trim();
    if (q) p.set('q', q);
    if ($('mcpHistoryKind').value) p.set('kind', $('mcpHistoryKind').value);
    if ($('mcpHistoryClient').value) p.set('client', $('mcpHistoryClient').value);
    return p.toString();
  }
  function openExchange(id) {
    chooseView('proxy'); chooseSub('history'); selectHistory(id);
  }
  function endpointCell(row, entry) {
    const td = document.createElement('td');
    td.className = 'host';
    const label = [entry.method, entry.url].filter(Boolean).join(' ');
    if (entry.exchange_id) {
      const link = document.createElement('button');
      link.className = 'agent-link';
      link.textContent = '#' + entry.exchange_id + (label ? ' · ' + label : '');
      link.title = 'Open in HTTP history: ' + (label || '#' + entry.exchange_id);
      link.onclick = event => { event.stopPropagation(); openExchange(entry.exchange_id); };
      td.append(link);
    } else {
      td.textContent = label || entry.summary || '—';
      td.title = td.textContent;
      if (!label && !entry.summary) td.className = 'muted';
    }
    row.append(td);
  }
  function detailRow(entry) {
    const row = document.createElement('tr'), td = document.createElement('td');
    row.className = 'agent-detail';
    td.colSpan = 6;
    const dl = document.createElement('dl');
    const add = (term, value) => { if (value === '' || value == null) return; const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = term; dd.textContent = value; dl.append(dt, dd); };
    const {access, ...args} = entry.details || {};
    add('Tool', entry.tool + ' · needs ' + (tierNames[entry.tier] || entry.tier) + (access ? ' · granted ' + (tierNames[access] || access) : ''));
    add('When', new Date(entry.time * 1000).toLocaleString());
    add('Client', entry.client || 'Unknown client');
    add('URL', entry.url);
    if (entry.status) add('Status', String(entry.status));
    add('Error', entry.error);
    add('Arguments', Object.keys(args).length ? JSON.stringify(args, null, 2) : 'None');
    td.append(dl);
    row.append(td);
    return row;
  }
  function renderHistory() {
    const key = JSON.stringify([historyItems, [...expanded], historyMore]);
    if (key === historyKey) return;
    historyKey = key;
    $('mcpActivity').replaceChildren();
    $('mcpActivityEmpty').hidden = !!historyItems.length;
    $('mcpHistoryMore').hidden = !historyMore;
    $('mcpHistoryCount').textContent = historyTotal ? historyTotal.toLocaleString() + (historyTotal === 1 ? ' call' : ' calls') : '';
    for (const entry of historyItems) {
      const row = document.createElement('tr');
      row.tabIndex = 0;
      row.setAttribute('aria-expanded', expanded.has(entry.id));
      row.classList.toggle('selected', expanded.has(entry.id));
      textCell(row, clockTime(entry.time), 'num time');
      textCell(row, entry.client || '—', entry.client ? 'host' : 'muted');
      textCell(row, entry.action || entry.tool.replace(/^dopamine_/, ''), entry.tier === 'read' ? '' : 'agent-change');
      endpointCell(row, entry);
      textCell(row, entry.ok ? (entry.status ? 'Completed · ' + entry.status : 'Completed') : 'Failed', entry.ok ? '' : 'error');
      textCell(row, entry.duration_ms + ' ms', 'num');
      row.onclick = () => { expanded.has(entry.id) ? expanded.delete(entry.id) : expanded.add(entry.id); renderHistory(); };
      row.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.click(); } };
      $('mcpActivity').append(row);
      if (expanded.has(entry.id)) $('mcpActivity').append(detailRow(entry));
    }
  }
  function renderClients(clients) {
    const select = $('mcpHistoryClient'), current = select.value;
    if (JSON.stringify(clients) === select.dataset.clients) return;
    select.dataset.clients = JSON.stringify(clients);
    select.replaceChildren(new Option('All clients', ''), ...clients.map(c => new Option(c, c)));
    select.value = clients.includes(current) ? current : '';
  }
  // The newest page is polled and merged, so older pages loaded with "Load older calls" stay put.
  async function loadHistory(reset = false) {
    const page = await api('mcp/history?' + historyParams());
    renderClients(page.clients);
    historyTotal = page.total_count;
    if (reset || !historyItems.length) {
      historyItems = page.items; historyMore = page.next_before_id;
    } else {
      const newest = historyItems[0].id;
      historyItems = page.items.filter(e => e.id > newest).concat(historyItems);
    }
    renderHistory();
  }
  async function load() {
    if (fetching || saving || view !== 'agents') return;
    fetching = true;
    try { settings = await api('mcp/settings'); render(); await loadHistory(); }
    catch (error) { $('mcpState').textContent = error.message; }
    finally { fetching = false; }
  }
  async function save(changes) {
    saving = true;
    $('mcpToggle').disabled = $('mcpPermission').disabled = true;
    try { settings = await api('mcp/settings', changes); render(); notify('Agent access updated'); }
    catch (error) { notify(error.message, true); render(); }
    finally { saving = false; $('mcpToggle').disabled = $('mcpPermission').disabled = false; }
  }
  button.onclick = () => { chooseView('agents'); load(); };
  let searchTimer;
  const refilter = () => { expanded.clear(); loadHistory(true).catch(error => notify(error.message, true)); };
  $('mcpHistorySearch').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(refilter, 250); };
  $('mcpHistoryKind').onchange = $('mcpHistoryClient').onchange = refilter;
  $('mcpHistoryMore').onclick = async () => {
    try {
      const page = await api('mcp/history?' + historyParams({before_id: historyMore}));
      historyItems = historyItems.concat(page.items); historyMore = page.next_before_id; renderHistory();
    } catch (error) { notify(error.message, true); }
  };
  // No confirmation dialogs in the app's web view: the first click arms Clear for a few seconds.
  $('mcpHistoryClear').onclick = async () => {
    if (!clearArmed) {
      $('mcpHistoryClear').textContent = 'Click again to clear';
      clearArmed = setTimeout(() => { clearArmed = null; $('mcpHistoryClear').textContent = 'Clear history'; }, 3000);
      return;
    }
    clearTimeout(clearArmed); clearArmed = null; $('mcpHistoryClear').textContent = 'Clear history';
    try { await api('mcp/history/clear', {}); expanded.clear(); await loadHistory(true); notify('Agent history cleared'); }
    catch (error) { notify(error.message, true); }
  };
  document.addEventListener('dopamine:project', () => { historyItems = []; expanded.clear(); if (view === 'agents') loadHistory(true).catch(() => {}); });
  $('mcpClient').onchange = showConfig;
  $('mcpToggle').onclick = () => settings && save({enabled: !settings.enabled});
  $('mcpPermission').onchange = () => save({access: $('mcpPermission').value});
  $('mcpCopy').onclick = () => settings && copy(settings.configs[$('mcpClient').value]);
  const source = document.createElement('option');
  source.value = 'mcp'; source.textContent = 'Agents'; $('sourceFilter').append(source);
  if (location.hash === '#agents') button.click();
  setInterval(load, 1500);
})();
