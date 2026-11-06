# Dopamine built-in MCP server · 0.1

Open **Dopamine.app → Agents**. Choose your client, copy its configuration, and add it to that client. Keep Dopamine running. The panel generates paths for the current app location, engine port and data directory.

Agent access starts enabled at **Read**. Choose a tier in **Agents → Permissions** or **Proxy → Agent Access**:

| Tier | Agents can |
| --- | --- |
| **Read** | Read history and search message contents, read exchanges, the site map, scope, rules, Repeater tabs and held messages; decode text. |
| **Workspace** | Also edit scope, draft match-and-replace rules, stage and edit Repeater tabs, and highlight or annotate history. Nothing is sent to targets. |
| **Active** | Also send requests and Repeater tabs, forward or drop held messages, and start or stop the proxy and Intercept. |
| **Everything** | Every tool with the guardrails below lifted: agents can create rules switched on (`enabled: true`) or turn them on, their edits keep a rule's state, and sends reach hosts outside the scope without `allow_out_of_scope`. |

At every tier, including Everything, agents work in the open project, cannot switch projects, and cannot send requests to Dopamine itself. Guardrails below Everything: rules an agent creates start **disabled**, an agent's edit to any rule turns it off, and agents cannot enable rules; a person turns them on in **Proxy → Match and replace**. `send_request` and `send_repeater_tab` refuse hosts outside the project scope unless the call passes `allow_out_of_scope: true`. Scope entries, rules, Repeater tabs and annotations created by an agent are marked **Agent** in the app.

**Disable access** rejects new MCP requests. Changes apply to subsequent calls; a network request already in progress can finish. Client configuration is not installed automatically.

## Connect Codex

With Dopamine in Applications, run:

```sh
codex mcp add dopamine -- /usr/bin/python3 "/Applications/Dopamine.app/Contents/Resources/dopamine_mcp.py"
```

Reconnect your Codex client after adding it. Alternatively, choose **Codex · config.toml** in the app and merge the generated section into your Codex configuration. Codex supports local stdio servers and the `command`/`args` configuration shown here. [Official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

## Connect Claude

For Claude Code, run this from the project where you want the integration:

```sh
claude mcp add --transport stdio dopamine -- /usr/bin/python3 "/Applications/Dopamine.app/Contents/Resources/dopamine_mcp.py"
```

For Claude Desktop, choose **Claude Desktop · JSON** in Dopamine and merge the generated `dopamine` entry into the client's MCP configuration, retaining any existing servers. Restart/reconnect the client. The same stdio configuration can be used in other local MCP hosts. [Official Claude MCP configuration documentation](https://code.claude.com/docs/en/mcp).

If the app moves, copy its configuration again from Agents so the bridge path matches its new location.

## Tools

| Tool | Purpose | Access |
| --- | --- | --- |
| `dopamine_get_status` | Listener, Intercept, history count, open project, access tier and limits | Read |
| `dopamine_get_project` | Open project name, file path and counts | Read |
| `dopamine_list_history` | List saved traffic; filter URL/method/status/source; in scope by default | Read |
| `dopamine_search_history` | Find text or a regex in request/response headers and decoded bodies | Read |
| `dopamine_get_exchange` | Read bounded raw request/response pages by history ID; `version: original` for rewritten exchanges | Read |
| `dopamine_list_targets` | Observed origins with counts, latest IDs and (optionally) paths | Read |
| `dopamine_get_scope` | Include and exclude host patterns | Read |
| `dopamine_list_rules` | Match-and-replace rules in order | Read |
| `dopamine_list_repeater_tabs` / `dopamine_get_repeater_tab` | Repeater tabs and their latest responses | Read |
| `dopamine_list_pending` / `dopamine_get_pending` | Held request/response messages | Read |
| `dopamine_transform_text` | Base64, URL, HTML, hex and SHA-256 | Read |
| `dopamine_set_scope` / `dopamine_add_to_scope` | Replace the scope or add one host | Workspace |
| `dopamine_create_rule` / `dopamine_update_rule` / `dopamine_delete_rule` / `dopamine_reorder_rules` | Draft and organize match-and-replace rules (agent rules start disabled) | Workspace |
| `dopamine_create_repeater_tab` / `dopamine_update_repeater_tab` | Stage a request in Repeater for the user | Workspace |
| `dopamine_annotate_exchange` | Highlight a history row and attach a note | Workspace |
| `dopamine_send_request` | Send a raw or structured HTTP request directly | Active |
| `dopamine_send_repeater_tab` | Send a Repeater tab and record the response in it | Active |
| `dopamine_set_proxy` | Start/stop the loopback listener | Active |
| `dopamine_set_intercept` | Enable/disable request holding | Active |
| `dopamine_resolve_pending` | Edit and forward, or drop, one held message | Active |

Every tool publishes input/output schemas and MCP behavior annotations. Results contain both JSON text and `structuredContent`. Execution/argument errors use `isError`; protocol errors use JSON-RPC error codes. Reading an exchange that contains a captured network failure is still a successful read. A failed send returns `isError: true` with the saved exchange and its history ID.

Example agent request:

> Find the last POST to /api/cart, inspect its response, then explain why it failed.

With Active selected:

> Send a GET to my local development server at http://127.0.0.1:8000/health and show its response.

`dopamine_send_request` accepts either:

```json
{"url":"http://127.0.0.1:8000/health","method":"GET","headers":[{"name":"Accept","value":"application/json"}]}
```

or:

```json
{"url":"http://127.0.0.1:8000","request":"GET /health HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n\r\n"}
```

These requests bypass Intercept and match-and-replace rules and appear in HTTP history with source **mcp**, selectable with the **Agents** source filter. To leave a request for the user to review and send, stage it with `dopamine_create_repeater_tab` instead.

With Workspace selected:

> Add *.shop.test to scope, and draft a rule that adds X-Debug: 1 to its requests.

The rule appears in **Proxy → Match and replace**, marked Agent and switched off.

For response interception: enable the listener and Intercept, read a request hold, then forward it with `intercept_response: true`. Its response appears as a new hold with `stage: "response"`; read and resolve that new ID. Request edits and response edits are validated against the current stage. Stopping with held traffic requires `drop_held: true`; disabling Intercept with held traffic requires `release_held: true` and forwards engine-held messages unchanged.

## Data and transport

- The HTTP endpoint is **http://127.0.0.1:18765/mcp**, served by the app's engine. It is a stateless Streamable HTTP endpoint returning JSON; it does not offer an SSE notification stream, OAuth flow, or server-initiated requests.
- Local clients normally use the bundled **stdio bridge**. It forwards newline-delimited JSON-RPC to the endpoint and reads the private bearer token automatically from `~/Library/Application Support/Dopamine/mcp-token` (mode 0600). No token is embedded in generated client configurations or shown in activity entries.
- HTTP clients must send that token as `Authorization: Bearer …`, `Content-Type: application/json`, and an `Accept` header containing both `application/json` and `text/event-stream`. Protocol versions supported: 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05. The endpoint checks Host and Origin and binds only to loopback. The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) defines the HTTP and stdio framing used here.
- Everything lives in the open project, a `.dopamine` SQLite file. History lists and searches use a descending ID cursor (`before_id` / `next_before_id`) over the whole project. Content search uses an SQLite FTS5 trigram index (substring matches) over headers and text bodies up to 1 MiB each; regex search runs over the same text. Target and held-queue lists use offsets. Default page size is 20, maximum 100.
- History, search and targets return in-scope traffic unless `include_out_of_scope: true` is passed. With no scope set, everything is in scope.
- gzip and deflate response bodies are decoded before they are saved. Exchanges changed by a match-and-replace rule have `rewritten: true`; `get_exchange` with `version: "original"` returns the message before the rules ran.
- Message reads return at most 16,000 characters per selected part by default, up to 65,536. Follow `next_offset` with `offset` to read more. Raw text is a UTF-8 view; it is not a lossless binary export. Base64 body copies are omitted from MCP results.
- The engine limits bodies and incoming HTTP JSON messages to 8 MiB. JSON escaping/framing counts toward the incoming-message limit. Network timeout is 30 seconds and held-message timeout is five minutes. The stdio bridge handles calls sequentially; it does not cancel an in-flight network exchange when a cancellation notification arrives.
- Repeater tabs are saved in the project a moment after each edit, so agents see them; unsaved Intercept editor drafts stay in the web view. Coordinate with the desktop user before resolving a hold they are editing.
- With HTTPS interception on (the default), decrypted HTTPS proxy traffic appears in history and Intercept like HTTP. Pass-through hosts remain CONNECT tunnels (`tunnel: true`). `dopamine_get_status` reports the mode in `https_proxy` and `tls_intercept`. Upstream certificate checks follow the app's setting.
- **Agent history** (Agents panel) records every tool call in the open project file, newest 10,000 per project: time, client (from the MCP `clientInfo`, sent by the bridge), tool, the tier it needs and the tier granted, what it did in words, the method, URL and status it touched, a link to the HTTP history exchange, duration, outcome and error. Arguments are kept with request text, bodies, notes, rule match/replace text and transform input reduced to their length, and headers reduced to their names. Filter by text, kind (reads, changes, sends, failures) or client, and clear it from the panel. The engine API is `GET /api/mcp/history` (`q`, `kind`, `client`, `before_id`, `limit`) and `POST /api/mcp/history/clear`. No filesystem, shell execution, scanning, project switching or history-deletion tool is exposed.

No Python packages are required by the shipped server or bridge. It uses the same `/usr/bin/python3` as Dopamine.

## Check and test

With the app open:

```sh
/usr/bin/python3 "/Applications/Dopamine.app/Contents/Resources/dopamine_mcp.py" --check
```

Expected: `ok: true`, server `dopamine`, 27 tools. If connection fails, open the app, enable Agents access, and check whether a custom port or data directory needs to be passed to the bridge. The engine and stdio bridge stop independently: closing an agent's bridge leaves the app running; quitting Dopamine ends access for all clients.

From the source folder:

```sh
/usr/bin/python3 -m unittest discover -v -s tests
node tests/test_curl.js
node tests/test_intercept_ui.js
/usr/bin/python3 verify_mcp_evaluation.py
```

Verification for the 0.6 preview: 28 backend/MCP integration tests, 10 cURL checks, 5 Intercept payload checks, and 10 deterministic read-only evaluation workflows. The official MCP Python SDK 1.30.0 also successfully negotiated 2025-11-25, discovered/validated all 11 schemas, read resources, sent a loopback request, and retrieved paginated content over both HTTP and stdio. The SDK was installed only in an isolated verification environment and is not shipped in the app. No paid model evaluation or live Claude/Codex account call was used.

Verification for the 0.7 preview (October 23, 2026): the same 28 tests and 10 evaluation workflows pass. Against the running app, the bundled bridge passed `--check` and a full stdio session (initialize on 2025-11-25, 11 tools with schemas, every read tool, and a refused control call in Read only mode). On an isolated engine, Read and control allowed `send_request` (saved as source `mcp` and shown under the Agents filter) and `set_intercept`. Disable access returned HTTP 403 to the bridge, and re-enabling restored it. Agent activity times now use the same 24-hour clock as HTTP history.

The new Agents panel passes browser layout inspection at 1440, 768 and 390 pixels. Its compact toolbar/metadata labels retain the existing desktop density; the inspector reports that as a warning. The dialog-shadow source warning refers to the existing floating cURL importer. Test fixtures remain isolated from the user's history.


Verification for 0.1.0 (October 28, 2026): 54 engine, HTTPS, project and MCP tests and the 10 evaluation workflows pass, including access-tier enforcement, agent-drafted rules starting disabled, the out-of-scope send guard and `send_repeater_tab`. Versions before 0.1.0 were development previews numbered 0.2–0.7.
