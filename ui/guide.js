/* Guide: a built-in how-to for each part of Dopamine, opened from the header's help button or Help → Dopamine Guide. */
(() => {
  const sections = [
    ['start', 'Get started', 'Capture your first request in a minute.', 'settings', 'Open Proxy settings', `
      <ol class="guide-steps">
        <li>Open <b>Proxy → Proxy settings</b> and click <b>Start proxy</b>. The listener runs on <code>127.0.0.1:18080</code>.</li>
        <li>Under <b>HTTPS interception</b>, click <b>Trust on this Mac…</b> and approve the macOS prompt so browsers accept decrypted HTTPS.</li>
        <li>Click <b>Open Browser</b> at the right of the Proxy tabs (<kbd>⇧⌘B</kbd>). Dopamine opens Chrome, Brave, Edge or Chromium with a fresh profile that already uses the proxy, so your own browsing stays out of it.</li>
        <li>Or point any client at <code>127.0.0.1:18080</code>, for example from a terminal:<code class="guide-block">curl --noproxy '' -x http://127.0.0.1:18080 https://example.com/</code></li>
        <li>Browse. Each request appears in <b>HTTP history</b>. Add the hosts you are testing to <b>Scope</b> so Intercept only stops on them.</li>
      </ol>`],
    ['projects', 'Projects', 'One file per engagement, saved as you go.', null, null, `
      <ul class="guide-list">
        <li>A project is a single <code>.dopamine</code> file holding HTTP history, Repeater tabs, scope and match-and-replace rules. Everything is saved as it happens; there is no Save command.</li>
        <li><b>File → New Project</b> (<kbd>⌘N</kbd>) starts one in <code>~/Documents/Dopamine Projects</code> with a dated name. <b>Rename or Move…</b> gives it a real name or puts it somewhere else.</li>
        <li><b>Open Project…</b> (<kbd>⌘O</kbd>) and <b>Open Recent</b> switch projects. The proxy keeps running and new traffic goes to the project you open. If messages are held, Dopamine asks whether to forward or drop them first.</li>
        <li>The project name is shown at the top of the window. The last project opens again when you start Dopamine.</li>
      </ul>`],
    ['scope', 'Scope', 'Tell Dopamine which hosts you are testing.', 'scope', 'Open Scope', `
      <ul class="guide-list">
        <li>Add hosts under <b>Proxy → Scope</b>, or right-click a request in HTTP history or a host in the site map. <code>*.example.com</code> also matches example.com; add a path prefix to narrow it further. Exclude wins over include.</li>
        <li>Intercept only holds in-scope requests, so analytics and other noise pass straight through.</li>
        <li>HTTP history shows <b>In scope only</b> once a scope is set. Untick it to see everything; out-of-scope traffic is still recorded unless you turn that off.</li>
        <li>With no hosts added, everything is in scope.</li>
      </ul>`],
    ['rules', 'Match and replace', 'Rewrite traffic automatically.', 'rules', 'Open Match and replace', `
      <ul class="guide-list">
        <li>Each rule finds text in a request or response line, header or body and replaces it. Tick <b>Regular expression</b> to use a pattern; <code>$1</code> inserts a group.</li>
        <li>Leave <b>Match</b> empty on a header rule to add a header, such as <code>X-Test: 1</code>. Leave <b>Replace</b> empty to remove the matching header.</li>
        <li>Rules run in order on proxy traffic, before Intercept, and only in scope unless you tick <b>Also out-of-scope traffic</b>. Repeater and agents send exactly what they are given.</li>
        <li>Rewritten requests are marked “rule” in HTTP history. Choose <b>Before rules</b> above the request to see the original.</li>
        <li>Rules drafted by an agent are marked <b>Agent</b> and start off until you turn them on.</li>
      </ul>`],
    ['https', 'HTTPS', 'How decryption works and how to skip it for some hosts.', 'settings', 'Open HTTPS settings', `
      <dl class="facts">
        <dt>Certificate</dt><dd>Dopamine creates its own certificate authority for this Mac and issues host certificates on demand. Trust it once with <b>Trust on this Mac…</b>.</dd>
        <dt>Firefox</dt><dd>Firefox and other devices keep their own certificate lists. Browse to <code>http://dopamine/</code> through the proxy, download the certificate and import it as an authority.</dd>
        <dt>Pass-through</dt><dd>Hosts listed under <b>Pass-through hosts</b> stay encrypted tunnels, which is useful for certificate-pinned apps. <code>*.apple.com</code> also matches <code>apple.com</code>.</dd>
        <dt>Test servers</dt><dd>Turn off <b>Verify server certificates</b> to reach servers with self-signed or internal certificates.</dd>
        <dt>Rejected</dt><dd>If a client rejects the certificate, a notice appears and the failed handshake is recorded in history.</dd>
      </dl>`],
    ['history', 'HTTP history', 'Everything that went through the proxy, Repeater or an agent.', 'history', 'Open HTTP history', `
      <ul class="guide-list">
        <li>Type in the filter to match URL, method or status. Use <b>Source</b> to show only proxy or Repeater traffic.</li>
        <li><b>Search requests and responses</b> looks inside headers and bodies. <b>Aa</b> matches case and <b>.*</b> takes a regular expression. Selecting a result jumps to the match.</li>
        <li>Right-click a request to add its host to scope, highlight it with a color, or add a note.</li>
        <li>Compressed responses (gzip, deflate) are shown decoded. Brotli is shown as received.</li>
        <li>Select a row to see the request and response. <b>Send to Repeater</b> copies the request into a new tab.</li>
        <li><b>Clear</b> deletes all saved requests from this Mac after you confirm. The site map empties with it; Repeater tabs are kept.</li>
        <li>The newest 500 matching requests are listed; <b>Load older requests</b> shows more.</li>
      </ul>`],
    ['intercept', 'Intercept', 'Hold requests and responses to change them before they are sent.', 'intercept', 'Open Intercept', `
      <ul class="guide-list">
        <li>Turn interception on with the toolbar button or <kbd>⌘I</kbd>. Requests through the proxy wait in <b>Held messages</b>.</li>
        <li>Edit the message or its destination, then <b>Forward</b> (<kbd>Ctrl</kbd> <kbd>F</kbd>) or <b>Drop</b> (<kbd>Ctrl</kbd> <kbd>D</kbd>). <b>Forward all</b> and <b>Drop all</b> act on every held message.</li>
        <li>Tick <b>Intercept this response</b> on a held request to hold and edit what the server sends back too.</li>
        <li>Held messages expire after five minutes. Repeater and agent requests are never held.</li>
      </ul>`],
    ['repeater', 'Repeater', 'Edit a request and send it again as often as you like.', 'repeater', 'Open Repeater', `
      <ul class="guide-list">
        <li>Set the <b>Target</b> URL, edit the raw request and press <b>Send</b> or <kbd>⌘</kbd> <kbd>↩</kbd>.</li>
        <li>Open a tab with <b>+</b>, <b>New tab</b> or <kbd>⌘T</kbd>. Double-click a tab to rename it; clear the name to show the path again.</li>
        <li><b>Previous</b> and <b>Next</b> step through the versions you sent from this tab. Your unsent edits are kept.</li>
        <li><b>Paste cURL</b> (<kbd>⇧⌘I</kbd>) turns a browser’s “Copy as cURL (bash)” into a tab.</li>
        <li>Tabs are saved in the project and come back when you reopen it. Tabs staged by an agent are marked <b>Agent</b>.</li>
      </ul>`],
    ['target', 'Site map', 'Observed traffic grouped by host and path.', 'target', 'Open Site map', `
      <ul class="guide-list">
        <li>Click a host or folder in the tree to list only its requests. <b>All targets</b> removes the filter.</li>
        <li><b>Clear</b> hides the current entries from the site map. HTTP history keeps them, and new traffic appears as it arrives.</li>
      </ul>`],
    ['messages', 'Reading messages', 'Every request and response pane has the same tools.', null, null, `
      <dl class="facts">
        <dt>Views</dt><dd><b>Raw</b>, <b>Pretty</b> (formatted JSON), <b>Hex</b>, <b>Headers</b>, <b>Body</b>, <b>Query params</b> and <b>Cookies</b>.</dd>
        <dt>Find</dt><dd>Search inside a message with its find field, or press <kbd>⌘F</kbd> to jump to the nearest search.</dd>
        <dt>Wrap</dt><dd>Wrap long lines instead of scrolling sideways.</dd>
      </dl>`],
    ['decoder', 'Decoder', 'Encode, decode and hash text locally.', 'decoder', 'Open Decoder', `
      <p>Choose Base64, URL component, HTML entities or hexadecimal, then <b>Encode</b> or <b>Decode</b>. <b>SHA-256</b> hashes the input. <b>Use output as input</b> chains steps together.</p>`],
    ['agents', 'Agents', 'Let Codex, Claude and other MCP clients work with your traffic.', 'agents', 'Open Agents', `
      <ul class="guide-list">
        <li>Pick your client on the Agents page, copy its configuration and reconnect the client.</li>
        <li>Access is <b>Read</b> by default: agents can search history and read messages. <b>Workspace</b> also lets them edit scope, draft rules, stage Repeater tabs and annotate history. <b>Active</b> also lets them send traffic and handle held messages. <b>Everything</b> removes the guardrails: agents can switch rules on and send outside the scope without confirming.</li>
        <li>Agents can't switch projects, and below Everything they must confirm before sending to a host outside the scope. Everything they change is marked <b>Agent</b>, and every call is kept in the project's <b>Agent history</b>, with the endpoints it touched.</li>
        <li>Turn access off any time with <b>Disable access</b> or <b>Proxy → Agent Access</b>.</li>
      </ul>`],
    ['layout', 'Layout', 'Arrange the panels the way you work.', null, null, `
      <ul class="guide-list">
        <li>Drag the line between two panels to resize them. Double-click it to go back to the default size.</li>
        <li>You can also focus a divider with <kbd>Tab</kbd> and move it with the arrow keys; hold <kbd>⇧</kbd> for bigger steps.</li>
        <li>Sizes are remembered. <kbd>⇧⌘L</kbd> switches between light and dark.</li>
      </ul>`],
    ['keys', 'Keyboard shortcuts', 'The ones worth learning first.', null, null, `
      <table class="guide-keys"><tbody>
        <tr><td><kbd>⌘1</kbd> – <kbd>⌘6</kbd></td><td>Site map, HTTP history, Intercept, Repeater, Decoder, Agents</td></tr>
        <tr><td><kbd>⌘N</kbd> / <kbd>⌘O</kbd></td><td>New project / open project</td></tr>
        <tr><td><kbd>⇧⌘B</kbd></td><td>Open Browser</td></tr>
        <tr><td><kbd>⇧⌘P</kbd></td><td>Start or stop the listener</td></tr>
        <tr><td><kbd>⌘I</kbd></td><td>Intercept on or off</td></tr>
        <tr><td><kbd>Ctrl</kbd> <kbd>F</kbd> / <kbd>Ctrl</kbd> <kbd>D</kbd></td><td>Forward / drop the selected held message</td></tr>
        <tr><td><kbd>⌘T</kbd></td><td>New Repeater tab</td></tr>
        <tr><td><kbd>⌘</kbd> <kbd>↩</kbd></td><td>Send in Repeater</td></tr>
        <tr><td><kbd>⇧⌘I</kbd></td><td>Import cURL</td></tr>
        <tr><td><kbd>⌘F</kbd></td><td>Find</td></tr>
        <tr><td><kbd>⌘,</kbd></td><td>Proxy settings</td></tr>
        <tr><td><kbd>⌘?</kbd></td><td>This guide</td></tr>
      </tbody></table>`],
    ['privacy', 'Data and limits', 'What stays on this Mac and what Dopamine doesn’t do yet.', null, null, `
      <dl class="facts">
        <dt>Storage</dt><dd>Projects (history, Repeater tabs, scope, rules) are <code>.dopamine</code> files, by default in <code>~/Documents/Dopamine Projects</code>. Settings and the certificate authority live in <code>~/Library/Application Support/Dopamine</code>. Everything listens on <code>127.0.0.1</code> only.</dd>
        <dt>System</dt><dd>Dopamine never changes your system proxy settings. Closing the window keeps it running in the menu bar; quit with <kbd>⌘Q</kbd>.</dd>
        <dt>Limits</dt><dd>HTTP/1.1 only (no HTTP/2 or WebSocket relay). Bodies up to 8 MiB; requests time out after 30 seconds.</dd>
      </dl>`],
  ];

  const panel = document.createElement('section');
  panel.id = 'guide';
  panel.className = 'view';
  panel.setAttribute('aria-label', 'Guide');
  panel.innerHTML = `<div class="guide-scroll"><div class="guide-intro"><h2>Using Dopamine</h2>
    <p>Dopamine sits between a browser or client and the servers it talks to. It records every request, lets you hold and change them, and replays them in Repeater. Use it only on systems you own or are authorized to test.</p>
    <nav class="guide-contents" aria-label="Guide contents">${sections.map(([id, title]) => `<a href="#guide-${id}">${title}</a>`).join('')}</nav></div>
    ${sections.map(([id, title, lead, go, label, body]) => `<section class="setting-row" id="guide-${id}"><div class="setting-label"><h2>${title}</h2><p>${lead}</p>${go ? `<button class="guide-go" data-go="${go}">${label}</button>` : ''}</div><div class="guide-body">${body}</div></section>`).join('')}
    </div>`;
  document.querySelector('main').append(panel);

  panel.addEventListener('click', e => {
    const link = e.target.closest('.guide-contents a');
    if (link) { e.preventDefault(); panel.querySelector(link.getAttribute('href')).scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
    const go = e.target.closest('[data-go]');
    if (go) window.dopamineNative.go(go.dataset.go);
  });

  const button = document.createElement('button');
  button.id = 'guideButton';
  button.className = 'icon-button';
  button.title = 'Guide';
  button.setAttribute('aria-label', 'Open the guide');
  button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01"/></svg>';
  button.onclick = () => window.dopamineNative.go('guide');
  document.getElementById('themeToggle').before(button);
  new MutationObserver(() => button.setAttribute('aria-pressed', panel.classList.contains('active'))).observe(panel, {attributes: true, attributeFilter: ['class']});
})();
