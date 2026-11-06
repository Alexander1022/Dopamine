# Changelog

All notable changes to Dopamine are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- **Agent history.** Every MCP tool call is saved in the project: time, client, what the agent did, the method, URL and status it touched with a link to the exchange in HTTP history, duration, outcome, error and redacted arguments. Filter by text, kind or client; load older calls; clear it from Agents.
- **Everything** access tier (Agents panel and Proxy → Agent Access): all tools without the guardrails. Agents can create rules switched on or turn them on, and send outside the scope without `allow_out_of_scope`.

### Changed

- The in-memory "Recent agent activity" list (200 calls, lost on quit) is replaced by Agent history.

## 0.1.0 — 2026-10-28

First public release. Development previews were numbered 0.2–0.7; this release restarts numbering for distribution.

### Added

- **Projects.** Each engagement is one `.dopamine` file (SQLite) holding HTTP history, Repeater tabs, scope and match-and-replace rules, written as it happens. File → New Project (auto-named in `~/Documents/Dopamine Projects`), Open Project, Open Recent, Rename or Move, Show in Finder; `.dopamine` files open from Finder. One project is open at a time; switching keeps the proxy running and asks whether to forward or drop held messages. The last project reopens at launch.
- **Scope** per project: include and exclude host globs with optional path prefixes (exclude wins). Intercept holds only in-scope requests, HTTP history and the site map show in-scope traffic by default, and recording out-of-scope traffic can be turned off. Right-click a request or site map host to add it.
- **Match and replace** per project: ordered rules for request and response lines, headers and bodies, literal or regex with `$1` groups; add or remove headers. Rules apply to in-scope proxy traffic before Intercept (optionally all traffic), never to Repeater or agents. History stores what was sent and received, marks rewritten exchanges, and keeps the original (**Before rules**).
- **Search** inside requests and responses (headers and decoded text bodies) with an FTS5 trigram index: substring, case-sensitive or regex, combined with the existing filters. Selecting a result jumps to the match. History pages in 500s with **Load older requests** instead of stopping at 2,000.
- **Decompression.** gzip and deflate responses are decoded for display, search and rules; proxied requests drop `Accept-Encoding` by default (Proxy settings).
- **Open Browser** (⇧⌘B): launches Chrome, Brave, Edge or Chromium with a throwaway profile on the proxy and the browser's background traffic off. Offers to trust the CA and starts the listener if needed.
- **Annotations**: highlight colors and notes on history rows.
- **install.sh**, attached to each release: verifies the zip against `SHA256SUMS.txt` and installs to `/Applications` or `~/Applications` without a password or a Gatekeeper prompt; `--uninstall` removes the app. Checked with shellcheck in CI.
- **Proxy** on `127.0.0.1:18080` with HTTP history, Site map, filtering, and Intercept for requests and, per request, responses (edit, change destination, forward, drop, forward/drop all).
- **HTTPS interception.** A per-install Dopamine certificate authority (RSA 2048, 10 years) issues leaf certificates per host (397 days, SAN for host names and IP addresses). Decrypted HTTPS flows through history, Intercept, Site map, Repeater and MCP like HTTP, with keep-alive on decrypted connections.
- **HTTPS settings** in Proxy settings: decrypt on/off, trust status, one-click trust and removal in the login keychain, Show in Finder, regenerate, pass-through host patterns, and upstream certificate verification.
- **http://dopamine/** through the proxy serves the CA certificate (PEM and DER) for Firefox and other clients.
- **Rejected-certificate notice** when a client refuses the Dopamine certificate, plus a history entry naming the host.
- **Repeater** tabs with per-tab send history, notes and cURL import; **Decoder** for Base64, URL, HTML entities, hex and SHA-256.
- **Agents:** a built-in MCP server with 27 `dopamine_*` tools and three access tiers: **Read** (default), **Workspace** (edit scope, draft rules, stage Repeater tabs, annotate) and **Active** (send traffic, resolve holds, control the proxy). Agent-drafted rules start disabled and only a person can enable them; sends to out-of-scope hosts need `allow_out_of_scope`; agents cannot switch projects. Agent changes are marked in the UI, and the activity log shows tier and target for each call. New tools include `search_history`, `get_scope`, `set_scope`, `create_rule`, `create_repeater_tab`, `send_repeater_tab` and `annotate_exchange`.
- Native macOS shell: menu bar item with held-message count, Dock badge, keyboard shortcuts, light and dark appearance, standard About panel, and **Proxy → HTTPS Certificate…**.
- **Local API token.** The engine's `/api` requires a secret created at each launch. The app hands it to the engine on stdin and to its own web view, which sends it in an `X-Dopamine-Token` header, so other local programs and web pages cannot drive Dopamine. MCP clients keep using the bearer token through the stdio bridge, and `dopamine_send_request` refuses to target Dopamine's own API or proxy port.
- Animated startup splash: the dopamine molecule draws itself while the engine starts (static when Reduce Motion is on), with a synthesized cinematic hit that lands, with a flash, as the molecule completes. Turn the sound off with **Dopamine → Startup Sound**.
- Universal binary (Apple Silicon and Intel); `release.sh` builds a DMG, zip and SHA-256 checksums.
- Engine, HTTPS, MCP, cURL and Intercept tests, run in CI on every push.

### Migration from development builds

- History (`history.sqlite3`) moves into `~/Documents/Dopamine Projects/Default.dopamine`, and Repeater tabs from the web view move into it on first launch. The old file is kept as `history.sqlite3.migrated`.
- Agent access set to "Read and control" becomes **Active**.
- An existing certificate authority is kept, so it stays trusted.
- MCP tools are renamed to `dopamine_*`; copy client configuration again from **Agents**. Test variables are `DOPAMINE_PORT` and `DOPAMINE_DATA_DIR`.

### Known limits

HTTP/1.1 only (no HTTP/2 or WebSocket relay), 8 MiB bodies, Brotli bodies shown as received, no fuzzer or comparer, ad-hoc signed (not notarized).
