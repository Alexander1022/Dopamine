/* HTTPS interception settings (Proxy settings), the certificate-rejected notice, and the version in the footer. */
(() => {
  const firstRow = document.querySelector('#settings .setting-row');
  const rows = document.createElement('div');
  rows.innerHTML = `
    <section class="setting-row" id="tlsSection"><div class="setting-label"><h2>HTTPS interception</h2><p>Dopamine decrypts HTTPS with a certificate authority created on this Mac. Trust it here so Safari, Chrome and command-line tools accept Dopamine certificates.</p></div>
    <div class="setting-panel"><div class="toolbar"><label><input id="tlsIntercept" type="checkbox"> Decrypt HTTPS traffic</label><span class="grow"></span><span id="tlsTrust" class="trust-pill">Checking…</span></div>
    <dl class="facts tls-facts"><dt>Certificate</dt><dd id="tlsName">—</dd><dt>SHA-256</dt><dd id="tlsFingerprint" class="mono-small">—</dd><dt>Expires</dt><dd id="tlsExpires">—</dd><dt>File</dt><dd id="tlsPath" class="mono-small">—</dd></dl>
    <div class="toolbar tls-actions"><button id="tlsTrustButton" class="primary">Trust on this Mac…</button><button id="tlsReveal">Show in Finder</button><span class="grow"></span><button id="tlsRegenerate">Regenerate…</button></div>
    <p class="tls-note">Trusting asks macOS for your password and adds the certificate to your login keychain. Firefox and other devices keep their own lists: browse to <code class="inline">http://dopamine/</code> through the proxy to download the certificate.</p>
    <p class="tls-note">Check it from a terminal:</p><code id="tlsCurl"></code></div></section>
    <section class="setting-row"><div class="setting-label"><h2>Pass-through and upstream</h2><p>Hosts listed here stay encrypted tunnels. Use this for apps that pin their certificates. One pattern per line; <code class="inline">*.example.com</code> also matches example.com.</p></div>
    <div class="setting-panel"><label class="sr-only" for="tlsPassthrough">Pass-through hosts</label><textarea id="tlsPassthrough" class="setting-text" spellcheck="false" placeholder="*.apple.com&#10;*.icloud.com"></textarea>
    <div class="toolbar tls-actions"><span id="tlsPassthroughState" class="muted tiny"></span><span class="grow"></span><button id="tlsPassthroughSave" disabled>Save hosts</button></div>
    <div class="toolbar tls-verify"><label><input id="tlsVerify" type="checkbox"> Verify server certificates</label></div>
    <p class="tls-note">Turn off to test servers with self-signed or internal certificates. Applies to the proxy, Repeater and agents. Leave on for public sites.</p></div></section>`;
  firstRow.after(...rows.children);

  const notice = document.createElement('div');
  notice.id = 'tlsNotice';
  notice.className = 'notice tls-notice';
  notice.hidden = true;
  notice.setAttribute('role', 'status');
  notice.innerHTML = '<span id="tlsNoticeText"></span><span class="grow"></span><button id="tlsNoticeOpen">HTTPS settings</button><button id="tlsNoticeDismiss" aria-label="Dismiss">Dismiss</button>';
  document.querySelector('main').prepend(notice);

  let tls = null, fetching = false, busy = false, savedHosts = '', dismissed = 0, confirmTimer;
  try { dismissed = Number(localStorage.getItem('dg.tlsDismissed')) || 0 } catch {}

  function render() {
    if (!tls) return;
    const ca = tls.ca || {};
    $('tlsIntercept').checked = tls.intercept;
    $('tlsVerify').checked = tls.verify_upstream;
    $('tlsName').textContent = ca.exists ? ca.name : 'Created when HTTPS interception is first used';
    $('tlsFingerprint').textContent = ca.sha256 || '—';
    $('tlsExpires').textContent = ca.expires || '—';
    $('tlsPath').textContent = ca.path || '—';
    const trusted = ca.trusted;
    $('tlsTrust').textContent = !ca.exists ? 'Not created' : trusted === true ? 'Trusted by macOS' : trusted === false ? 'Not trusted' : 'Trust unknown';
    $('tlsTrust').classList.toggle('ok', trusted === true);
    $('tlsTrust').classList.toggle('warn', ca.exists && trusted === false);
    $('tlsTrustButton').textContent = trusted ? 'Remove trust…' : 'Trust on this Mac…';
    $('tlsTrustButton').classList.toggle('primary', !trusted);
    $('tlsCurl').textContent = `curl --noproxy '' -x http://127.0.0.1:${status.proxy_port || 18080} --cacert ${shellQuote(ca.path || '')} https://example.com/`;
    const hosts = tls.passthrough.join('\n');
    if (hosts !== savedHosts && $('tlsPassthrough').value === savedHosts) $('tlsPassthrough').value = hosts;
    savedHosts = hosts;
    hostsChanged();
  }
  function shellQuote(text) { return /^[\w@%+=:,./-]+$/.test(text) ? text : "'" + text.replace(/'/g, "'\\''") + "'" }
  function hostsList() { return $('tlsPassthrough').value.split(/[\n,]/).map(h => h.trim()).filter(Boolean) }
  function hostsChanged() {
    const dirty = hostsList().join('\n') !== savedHosts;
    $('tlsPassthroughSave').disabled = !dirty || busy;
    $('tlsPassthroughState').textContent = dirty ? 'Unsaved changes' : tls && tls.passthrough.length ? tls.passthrough.length + (tls.passthrough.length === 1 ? ' host pattern' : ' host patterns') : 'No pass-through hosts';
  }
  async function load() {
    if (fetching || busy) return;
    fetching = true;
    try { tls = await api('tls'); render() } catch (error) { $('tlsTrust').textContent = error.message }
    finally { fetching = false }
  }
  async function act(path, data, message) {
    if (busy) return;
    busy = true;
    for (const b of document.querySelectorAll('#tlsSection button, .tls-actions button')) b.disabled = true;
    try { const result = await api(path, data); if (result && result.ca) { tls = result } notify(message) }
    catch (error) { notify(error.message, true) }
    finally { busy = false; for (const b of document.querySelectorAll('#tlsSection button')) b.disabled = false; await load(); render() }
  }
  $('tlsIntercept').onchange = () => act('tls', {intercept: $('tlsIntercept').checked}, $('tlsIntercept').checked ? 'HTTPS interception on. New HTTPS connections are decrypted.' : 'HTTPS interception off. HTTPS passes through encrypted.');
  $('tlsVerify').onchange = () => act('tls', {verify_upstream: $('tlsVerify').checked}, $('tlsVerify').checked ? 'Server certificates are verified.' : 'Server certificates are no longer verified. Use this only for test servers.');
  $('tlsPassthrough').oninput = hostsChanged;
  $('tlsPassthroughSave').onclick = () => act('tls', {passthrough: hostsList()}, 'Pass-through hosts saved. They apply to new connections.').then(() => { $('tlsPassthrough').value = savedHosts });
  $('tlsTrustButton').onclick = () => {
    const removing = !!(tls && tls.ca && tls.ca.trusted);
    notify(removing ? 'Confirm in the macOS dialog to remove trust…' : 'Confirm in the macOS dialog to trust the Dopamine certificate…');
    act('tls/' + (removing ? 'untrust' : 'trust'), {}, removing ? 'The Dopamine certificate is no longer trusted.' : 'The Dopamine certificate is trusted. Restart your browser if it still shows warnings.');
  };
  $('tlsReveal').onclick = () => act('tls/reveal', {}, 'Certificate shown in Finder.');
  $('tlsRegenerate').onclick = () => {
    const b = $('tlsRegenerate');
    if (b.dataset.confirm) {
      clearTimeout(confirmTimer); delete b.dataset.confirm; b.textContent = 'Regenerate…'; b.classList.remove('danger');
      return act('tls/regenerate', {}, 'New certificate authority created. Trust it again; the old one no longer signs anything.');
    }
    b.dataset.confirm = '1'; b.textContent = 'Replace certificate'; b.classList.add('danger');
    notify(tls && tls.ca && tls.ca.trusted ? 'Remove trust first if you are replacing a trusted certificate. Click again to replace.' : 'Click again to replace the certificate authority. Clients that trust the old one must trust the new one.');
    confirmTimer = setTimeout(() => { delete b.dataset.confirm; b.textContent = 'Regenerate…'; b.classList.remove('danger') }, 5000);
  };

  function renderNotice() {
    const r = status.tls_rejection;
    const show = !!(r && status.tls_intercept && r.time > dismissed && Date.now() / 1000 - r.time < 900);
    notice.hidden = !show;
    if (show) $('tlsNoticeText').textContent = `A client rejected the Dopamine certificate for ${r.host}. Trust the certificate, or add the host to pass-through.`;
  }
  $('tlsNoticeOpen').onclick = () => { chooseView('proxy'); chooseSub('settings'); load(); $('tlsSection').scrollIntoView({block: 'start'}) };
  $('tlsNoticeDismiss').onclick = () => {
    dismissed = status.tls_rejection ? status.tls_rejection.time : Date.now() / 1000;
    try { localStorage.setItem('dg.tlsDismissed', String(dismissed)) } catch {}
    renderNotice();
  };

  const baseRenderStatus = renderStatus;
  renderStatus = function () {
    baseRenderStatus();
    if (status.proxy_running && status.tls_intercept) $('proxyStatus').textContent += ' · HTTPS decrypted';
    renderNotice();
    if (status.version) $('footerEngine').textContent = 'Dopamine ' + status.version + ' · Engine ' + location.host;
  };
  const baseChooseSub = chooseSub;
  chooseSub = function (name) { baseChooseSub(name); if (name === 'settings') load() };
  setInterval(() => { if (view === 'proxy' && sub === 'settings') load() }, 3000);
  load();
})();
