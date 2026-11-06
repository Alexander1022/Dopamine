"""Dopamine project files: one .dopamine SQLite file per engagement with history, Repeater tabs, scope and rules."""
import base64
import fnmatch
import json
import re
import sqlite3
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

SCHEMA = 1
EXTENSION = '.dopamine'
INDEX_LIMIT = 1024 * 1024  # Each body is indexed for search up to 1 MiB.
TEXT_TYPES = ('text/', 'json', 'xml', 'javascript', 'ecmascript', 'x-www-form-urlencoded', 'graphql', 'csv', 'yaml')
SUMMARY = ('id', 'time', 'source', 'method', 'url', 'status', 'length', 'duration_ms', 'error', 'tunnel', 'rewritten', 'encoding', 'color', 'note', 'annotated_by')
COLORS = ('', 'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'gray')
ENABLE_REFUSED = 'Agents cannot turn rules on below Everything access. Ask the user to enable it in Proxy → Match and replace.'
RULE_TARGETS = ('request_line', 'request_header', 'request_body', 'response_line', 'response_header', 'response_body')
DEFAULT_SCOPE = dict(include=[], exclude=[], record_out_of_scope=True)
AGENT_FIELDS = ('time', 'client', 'tool', 'tier', 'ok', 'duration_ms', 'summary', 'action', 'method', 'url', 'status', 'exchange_id', 'details', 'error')
AGENT_HISTORY_LIMIT = 10000  # Newest agent calls kept per project.
AGENT_KINDS = {'read': "tier='read'", 'change': "tier<>'read' AND tool NOT IN ('dopamine_send_request', 'dopamine_send_repeater_tab')",
               'send': "tool IN ('dopamine_send_request', 'dopamine_send_repeater_tab')", 'failed': 'ok=0'}


def head_and_body(raw):
    gap = re.search(r'\r?\n\r?\n', raw)
    return (raw[:gap.start()], raw[gap.end():]) if gap else (raw, '')


def header_value(head, name):
    for line in head.splitlines()[1:]:
        key, _, value = line.partition(':')
        if key.strip().lower() == name:
            return value.strip()
    return ''


def searchable(raw, body):
    """Request or response text for the search index: head always, body only when it looks like text."""
    head, text = head_and_body(raw)
    kind = header_value(head, 'content-type').lower()
    binary = body is not None and b'\x00' in body[:8192]
    if binary or (kind and not any(t in kind for t in TEXT_TYPES)):
        return head
    return head + '\n\n' + text[:INDEX_LIMIT]


def clean_scope_entry(entry):
    if isinstance(entry, str):
        host, _, path = entry.strip().partition(' ')
        entry = dict(host=host, path=path.strip())
    if not isinstance(entry, dict) or set(entry) - {'host', 'path', 'agent'}:
        raise ValueError('Scope entries need a host pattern and an optional path prefix')
    host, path = str(entry.get('host', '')).strip().lower().rstrip('.'), str(entry.get('path', '') or '').strip()
    if not host or len(host) > 253 or any(c.isspace() or c in '/@' for c in host):
        raise ValueError('Invalid scope host pattern: ' + host)
    if path and (not path.startswith('/') or any(c.isspace() for c in path)):
        raise ValueError('Scope path prefixes start with /: ' + path)
    cleaned = dict(host=host, path=path)
    if entry.get('agent'):
        cleaned['agent'] = True
    return cleaned


def host_matches(host, pattern):
    host = (host or '').lower().rstrip('.')
    return fnmatch.fnmatchcase(host, pattern) or (pattern.startswith('*.') and host == pattern[2:])


def clean_rule(rule, existing=None):
    """Validate a match-and-replace rule. Regex patterns are compiled here so a bad one is refused on save."""
    allowed = {'id', 'enabled', 'target', 'match', 'replace', 'regex', 'comment', 'all_traffic', 'agent'}
    if not isinstance(rule, dict) or set(rule) - allowed:
        raise ValueError('Rules accept: ' + ', '.join(sorted(allowed - {'agent'})))
    merged = dict(existing or dict(id=uuid.uuid4().hex[:12], enabled=True, target='request_header', match='', replace='',
                                   regex=False, comment='', all_traffic=False))
    merged.update({k: v for k, v in rule.items() if k != 'id'})
    for key in ('enabled', 'regex', 'all_traffic'):
        if not isinstance(merged[key], bool):
            raise ValueError(key + ' must be true or false')
    for key in ('match', 'replace', 'comment'):
        if not isinstance(merged[key], str) or len(merged[key]) > 8192:
            raise ValueError(key + ' must be text up to 8192 characters')
    if merged['target'] not in RULE_TARGETS:
        raise ValueError('target must be one of: ' + ', '.join(RULE_TARGETS))
    if any(c in merged['replace'] for c in '\r\n') and not merged['target'].endswith('_body'):
        raise ValueError('Only body rules can insert line breaks')
    if not merged['match'] and not (merged['target'].endswith('_header') and merged['replace']):
        raise ValueError('Enter text to match. An empty match only adds a header.')
    if merged['regex']:
        try:
            re.compile(merged['match'])
        except re.error as exc:
            raise ValueError('Invalid regular expression: ' + str(exc))
    return merged


class Project:
    """One open project file. All access goes through its lock; the engine swaps whole Project objects to switch."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.create_function('regexp', 3, self._regexp, deterministic=True)
        self.db.create_function('in_scope', 1, self.in_scope)
        self.patterns = {}
        self._create()
        self.scope = self._meta('scope', DEFAULT_SCOPE)
        self.rules = self._meta('rules', [])
        self.revision = 0  # Bumped when tabs, scope or rules change, so the UI can reload edits made by agents.

    @property
    def name(self):
        return self.path.stem

    def _create(self):
        with self.lock:
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and 'meta' not in tables:
                raise ValueError(self.path.name + ' is not a Dopamine project')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, source TEXT NOT NULL, method TEXT NOT NULL,
                    url TEXT NOT NULL, host TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL, length INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL, content_type TEXT NOT NULL, error TEXT NOT NULL, tunnel INTEGER NOT NULL,
                    rewritten INTEGER NOT NULL, encoding TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL,
                    request_body BLOB, response_body BLOB, original_request TEXT, original_response TEXT,
                    color TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', annotated_by TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS history_host ON history(host);
                CREATE TABLE IF NOT EXISTS history_text (id INTEGER PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS repeater_tabs (id TEXT PRIMARY KEY, position INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, client TEXT NOT NULL, tool TEXT NOT NULL,
                    tier TEXT NOT NULL, ok INTEGER NOT NULL, duration_ms INTEGER NOT NULL, summary TEXT NOT NULL,
                    action TEXT NOT NULL, method TEXT NOT NULL, url TEXT NOT NULL, status INTEGER NOT NULL,
                    exchange_id INTEGER, details TEXT NOT NULL, error TEXT NOT NULL);
            ''')
            # Substring search uses an FTS5 trigram index when this SQLite has one (3.34+); otherwise it scans history_text.
            try:
                self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(request, response, content='history_text', content_rowid='id', tokenize='trigram')")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('created', ?)", (str(time.time()),))
            self.db.commit()
            if int(self._meta('schema', SCHEMA)) > SCHEMA:
                raise ValueError(self.path.name + ' was made by a newer Dopamine')

    def _meta(self, key, default):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if not row:
            return json.loads(json.dumps(default))
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def _set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, json.dumps(value)))
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def info(self):
        with self.lock:
            count = self.db.execute('SELECT COUNT(*) FROM history').fetchone()[0]
            tabs = self.db.execute('SELECT COUNT(*) FROM repeater_tabs').fetchone()[0]
            return dict(name=self.name, path=str(self.path), history_count=count, tab_count=tabs, revision=self.revision,
                        created=float(self._meta('created', 0)), legacy_tabs=self._meta('legacy_tabs', '') == 'pending',
                        search_index='fts5-trigram' if self.fts else 'scan')

    # History

    def save(self, item):
        url = item.get('url', '') or ''
        parsed = urllib.parse.urlsplit(url)
        request_body = base64.b64decode(item['request_body_base64']) if item.get('request_body_base64') else None
        response_body = base64.b64decode(item['response_body_base64']) if item.get('response_body_base64') else None
        response = item.get('response', '') or ''
        content_type = header_value(head_and_body(response)[0], 'content-type') if response.startswith('HTTP/') else ''
        row = (item.get('time') or time.time(), item.get('source', 'proxy'), item.get('method', ''), url, parsed.netloc.lower(),
               parsed.path or '/', int(item.get('status') or 0), int(item.get('length') or 0), int(item.get('duration_ms') or 0),
               content_type, item.get('error', '') or '', int(bool(item.get('tunnel'))), int(bool(item.get('rewritten'))),
               item.get('encoding', '') or '', item.get('request', '') or '', response, request_body, response_body,
               item.get('original_request'), item.get('original_response'))
        with self.lock:
            cur = self.db.execute('INSERT INTO history (time, source, method, url, host, path, status, length, duration_ms, content_type, error, '
                                  'tunnel, rewritten, encoding, request, response, request_body, response_body, original_request, original_response) '
                                  'VALUES (' + ','.join('?' * len(row)) + ')', row)
            ident = cur.lastrowid
            texts = (ident, searchable(row[14], request_body), searchable(response, response_body))
            self.db.execute('INSERT INTO history_text VALUES (?, ?, ?)', texts)
            if self.fts:
                self.db.execute('INSERT INTO history_fts (rowid, request, response) VALUES (?, ?, ?)', texts)
            self.db.commit()
        return ident

    def get(self, ident):
        with self.lock:
            cursor = self.db.execute('SELECT * FROM history WHERE id=?', (ident,))
            row = cursor.fetchone()
            if not row:
                raise KeyError('History item not found')
            item = dict(zip([c[0] for c in cursor.description], row))
        for part in ('request', 'response'):
            body = item.pop(part + '_body')
            if body is not None:
                item[part + '_body_base64'] = base64.b64encode(body).decode()
        item['tunnel'], item['rewritten'] = bool(item['tunnel']), bool(item['rewritten'])
        item['in_scope'] = self.in_scope(item['url'])
        return item

    def _regexp(self, pattern, flags, text):
        key = (pattern, flags)
        if key not in self.patterns:
            if len(self.patterns) > 32:
                self.patterns.clear()
            self.patterns[key] = re.compile(pattern, flags)
        return text is not None and self.patterns[key].search(text) is not None

    def query(self, query='', method=None, source=None, status=None, host=None, in_scope=False, search=None, search_in='both',
              regex=False, case_sensitive=False, before_id=None, limit=500):
        """History summaries, newest first, with filters applied in SQL. Returns (items, total_count, has_more)."""
        filters, values = [], []
        if query:
            filters.append("instr(lower(h.url || ' ' || h.method || ' ' || h.status), ?) > 0")
            values.append(query.lower())
        for column, value in (('method', method and method.upper()), ('source', source), ('status', status), ('host', host and host.lower())):
            if value is not None and value != '':
                filters.append('h.' + column + ' = ?')
                values.append(value)
        if in_scope:
            filters.append('in_scope(h.url)')
        join = ''
        if search:
            columns = {'request': ('request',), 'response': ('response',)}.get(search_in, ('request', 'response'))
            join = ' JOIN history_text t ON t.id = h.id'
            if regex:
                try:
                    re.compile(search)
                except re.error as exc:
                    raise ValueError('Invalid regular expression: ' + str(exc))
                flags = 0 if case_sensitive else re.IGNORECASE
                filters.append('(' + ' OR '.join('regexp(?, ?, t.' + c + ')' for c in columns) + ')')
                for _ in columns:
                    values += [search, flags]
            else:
                if self.fts and len(search) >= 3:
                    phrase = '"' + search.replace('"', '""') + '"'
                    match = phrase if len(columns) == 2 else columns[0] + ' : ' + phrase
                    filters.append('h.id IN (SELECT rowid FROM history_fts WHERE history_fts MATCH ?)')
                    values.append(match)
                compare = '(' + ' OR '.join(('instr(t.' + c + ', ?) > 0') if case_sensitive else ('instr(lower(t.' + c + '), ?) > 0') for c in columns) + ')'
                filters.append(compare)
                values += [search if case_sensitive else search.lower()] * len(columns)
        where = ' WHERE ' + ' AND '.join(filters) if filters else ''
        with self.lock:
            total = self.db.execute('SELECT COUNT(*) FROM history h' + join + where, values).fetchone()[0]
            if before_id is not None:
                where += (' AND ' if where else ' WHERE ') + 'h.id < ?'
                values = values + [before_id]
            rows = self.db.execute('SELECT ' + ','.join('h.' + c for c in SUMMARY) + ' FROM history h' + join + where +
                                   ' ORDER BY h.id DESC LIMIT ?', values + [limit + 1]).fetchall()
        items = []
        for row in rows[:limit]:
            item = dict(zip(SUMMARY, row))
            item['tunnel'], item['rewritten'] = bool(item['tunnel']), bool(item['rewritten'])
            item['in_scope'] = self.in_scope(item['url'])
            items.append(item)
        return items, total, len(rows) > limit

    def count(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM history').fetchone()[0]

    def clear(self):
        with self.lock:
            self.db.execute('DELETE FROM history')
            self.db.execute('DELETE FROM history_text')
            if self.fts:
                self.db.execute("INSERT INTO history_fts (history_fts) VALUES ('delete-all')")
            self.db.commit()

    def annotate(self, ident, color=None, note=None, by='user'):
        if color is not None and color not in COLORS:
            raise ValueError('color must be one of: ' + ', '.join(c or '(none)' for c in COLORS))
        if note is not None and (not isinstance(note, str) or len(note) > 4000):
            raise ValueError('note must be text up to 4000 characters')
        with self.lock:
            if not self.db.execute('SELECT 1 FROM history WHERE id=?', (ident,)).fetchone():
                raise KeyError('History item not found')
            if color is not None:
                self.db.execute('UPDATE history SET color=? WHERE id=?', (color, ident))
            if note is not None:
                self.db.execute('UPDATE history SET note=? WHERE id=?', (note, ident))
            self.db.execute('UPDATE history SET annotated_by=? WHERE id=?', (by if (color or note) else '', ident))
            self.db.commit()
        return self.get(ident)

    # Agent history: one row per MCP tool call, kept in the project next to the traffic it touched.

    def log_agent(self, entry):
        values = [entry.get(k, '') for k in AGENT_FIELDS]
        values[AGENT_FIELDS.index('ok')] = 1 if entry.get('ok') else 0
        values[AGENT_FIELDS.index('status')] = int(entry.get('status') or 0)
        values[AGENT_FIELDS.index('exchange_id')] = entry.get('exchange_id')
        values[AGENT_FIELDS.index('details')] = json.dumps(entry.get('details') or {})
        with self.lock:
            cursor = self.db.execute('INSERT INTO agent_history (' + ', '.join(AGENT_FIELDS) + ') VALUES (' + ', '.join('?' * len(AGENT_FIELDS)) + ')', values)
            if cursor.lastrowid % 100 == 0:
                self.db.execute('DELETE FROM agent_history WHERE id <= ?', (cursor.lastrowid - AGENT_HISTORY_LIMIT,))
            self.db.commit()
            return cursor.lastrowid

    def agent_history(self, before_id=None, limit=50, query='', kind='', client=''):
        if kind and kind not in AGENT_KINDS:
            raise ValueError('kind must be one of: ' + ', '.join(AGENT_KINDS))
        where, args = [], []
        if kind:
            where.append(AGENT_KINDS[kind])
        if client:
            where.append('client=?'); args.append(client)
        if query:
            where.append("(tool LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' OR error LIKE ? ESCAPE '\\')")
            like = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            args += [like] * 4
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        page = (clause + (' AND' if where else ' WHERE') + ' id < ?', args + [int(before_id)]) if before_id else (clause, args)
        limit = max(1, min(int(limit), 200))
        with self.lock:
            rows = self.db.execute('SELECT id, ' + ', '.join(AGENT_FIELDS) + ' FROM agent_history' + page[0] + ' ORDER BY id DESC LIMIT ?', page[1] + [limit + 1]).fetchall()
            total = self.db.execute('SELECT COUNT(*) FROM agent_history' + clause, args).fetchone()[0]
            clients = [r[0] for r in self.db.execute("SELECT DISTINCT client FROM agent_history WHERE client<>'' ORDER BY client")]
        items = []
        for row in rows[:limit]:
            item = dict(zip(('id',) + AGENT_FIELDS, row))
            item['ok'] = bool(item['ok'])
            item['details'] = json.loads(item['details'] or '{}')
            items.append(item)
        more = len(rows) > limit
        return dict(items=items, total_count=total, has_more=more, next_before_id=items[-1]['id'] if more else None, clients=clients)

    def clear_agent_history(self):
        with self.lock:
            self.db.execute('DELETE FROM agent_history')
            self.db.commit()

    def site_map(self, in_scope=True):
        """Hosts with their observed paths and counts, from history."""
        with self.lock:
            rows = self.db.execute('SELECT url, host, path, COUNT(*), MAX(id) FROM history WHERE tunnel = 0 AND host != \'\' ' +
                                   ('AND in_scope(url) ' if in_scope else '') + 'GROUP BY host, path ORDER BY host, path').fetchall()
        hosts = {}
        for url, host, path, count, latest in rows:
            origin = urllib.parse.urlunsplit((urllib.parse.urlsplit(url).scheme, host, '', '', ''))
            entry = hosts.setdefault(origin, dict(origin=origin, count=0, latest_id=0, paths=[]))
            entry['count'] += count
            entry['latest_id'] = max(entry['latest_id'], latest)
            entry['paths'].append(dict(path=path, count=count, latest_id=latest))
        return list(hosts.values())

    # Scope

    def in_scope(self, url):
        scope = self.scope
        try:
            parsed = urllib.parse.urlsplit(url or '')
            host, path = parsed.hostname or '', parsed.path or '/'
        except ValueError:
            return False

        def hit(entry):
            return host_matches(host, entry['host']) and (not entry['path'] or path.startswith(entry['path']))
        if any(hit(e) for e in scope['exclude']):
            return False
        return not scope['include'] or any(hit(e) for e in scope['include'])

    def set_scope(self, include=None, exclude=None, record_out_of_scope=None, agent=False):
        scope = json.loads(json.dumps(self.scope))
        for key, entries in (('include', include), ('exclude', exclude)):
            if entries is None:
                continue
            if not isinstance(entries, list) or len(entries) > 500:
                raise ValueError(key + ' must be a list of up to 500 entries')
            cleaned = []
            for entry in entries:
                entry = clean_scope_entry(entry)
                previous = next((e for e in scope[key] if e['host'] == entry['host'] and e['path'] == entry['path']), None)
                if agent and not previous:
                    entry['agent'] = True
                elif previous and previous.get('agent') and not agent:
                    entry['agent'] = True
                if not any(e['host'] == entry['host'] and e['path'] == entry['path'] for e in cleaned):
                    cleaned.append(entry)
            scope[key] = cleaned
        if record_out_of_scope is not None:
            if not isinstance(record_out_of_scope, bool):
                raise ValueError('record_out_of_scope must be true or false')
            scope['record_out_of_scope'] = record_out_of_scope
        with self.lock:
            self.scope = scope
            self._set_meta('scope', scope)
            self.revision += 1
        return scope

    def add_to_scope(self, host, path='', exclude=False, agent=False):
        key = 'exclude' if exclude else 'include'
        entries = list(self.scope[key]) + [dict(host=host, path=path, **({'agent': True} if agent else {}))]
        return self.set_scope(**{key: entries}, agent=agent)

    # Match-and-replace rules

    def save_rules(self, rules):
        with self.lock:
            self.rules = rules
            self._set_meta('rules', rules)
            self.revision += 1
        return rules

    def create_rule(self, rule, agent=False, may_enable=False):
        """may_enable is the Everything tier: the agent's rule can start on when it asks for that."""
        if agent and rule.get('enabled') is True and not may_enable:
            raise ValueError(ENABLE_REFUSED)
        on = rule.get('enabled') is True
        rule = clean_rule({k: v for k, v in rule.items() if k != 'agent'})
        if agent:
            rule.update(enabled=on, agent=True)  # Agents draft rules; a person turns them on.
        return self.save_rules(self.rules + [rule])[-1]

    def update_rule(self, ident, changes, agent=False, may_enable=False):
        rules = list(self.rules)
        index = next((i for i, r in enumerate(rules) if r['id'] == ident), None)
        if index is None:
            raise KeyError('Rule not found')
        if agent and changes.get('enabled') is True and not may_enable:
            raise ValueError(ENABLE_REFUSED)
        rule = clean_rule({k: v for k, v in changes.items() if k != 'agent'}, rules[index])
        if agent:
            # An agent's edit is a new draft: it turns the rule off until a person reviews it and turns it back on.
            rule.update(agent=True, **({} if may_enable else dict(enabled=False)))
        rules[index] = rule
        return self.save_rules(rules)[index]

    def delete_rule(self, ident):
        if not any(r['id'] == ident for r in self.rules):
            raise KeyError('Rule not found')
        self.save_rules([r for r in self.rules if r['id'] != ident])

    def reorder_rules(self, ids):
        if sorted(ids) != sorted(r['id'] for r in self.rules):
            raise ValueError('List every rule ID exactly once')
        by_id = {r['id']: r for r in self.rules}
        return self.save_rules([by_id[i] for i in ids])

    def replace_rules(self, rules):
        """The UI saves the whole list; agent markers on rules it already had are kept."""
        if not isinstance(rules, list) or len(rules) > 200:
            raise ValueError('rules must be a list of up to 200 rules')
        known = {r['id']: r for r in self.rules}
        cleaned = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError('Each rule must be an object')
            previous = known.get(rule.get('id'))
            item = clean_rule({k: v for k, v in rule.items() if k != 'agent'}, previous)
            if previous and 'id' in rule:
                item['id'] = previous['id']
            cleaned.append(item)
        return self.save_rules(cleaned)

    # Repeater tabs

    def tabs(self):
        with self.lock:
            rows = self.db.execute('SELECT id, data FROM repeater_tabs ORDER BY position').fetchall()
        return [dict(json.loads(data), id=ident) for ident, data in rows]

    def tab(self, ident):
        with self.lock:
            row = self.db.execute('SELECT data FROM repeater_tabs WHERE id=?', (ident,)).fetchone()
        if not row:
            raise KeyError('Repeater tab not found')
        return dict(json.loads(row[0]), id=ident)

    def save_tabs(self, tabs, bump=False):
        if not isinstance(tabs, list) or len(tabs) > 200:
            raise ValueError('tabs must be a list of up to 200 tabs')
        for tab in tabs:
            if not isinstance(tab, dict) or not isinstance(tab.get('id'), str) or not isinstance(tab.get('url', ''), str) or not isinstance(tab.get('request', ''), str):
                raise ValueError('Each tab needs an id, url and request')
        with self.lock:
            self.db.execute('DELETE FROM repeater_tabs')
            for position, tab in enumerate(tabs):
                data = {k: v for k, v in tab.items() if k != 'id'}
                if isinstance(data.get('response'), str):
                    data['response'] = data['response'][:100000]
                self.db.execute('INSERT INTO repeater_tabs VALUES (?, ?, ?)', (tab['id'], position, json.dumps(data)))
            self.db.commit()
            if bump:
                self.revision += 1
        return self.revision

    def put_tab(self, tab):
        """Insert or replace one tab (used by agents); bumps the revision so the UI reloads."""
        with self.lock:
            tabs = self.tabs()
            index = next((i for i, t in enumerate(tabs) if t['id'] == tab['id']), None)
            if index is None:
                tabs.append(tab)
            else:
                tabs[index] = tab
            self.save_tabs(tabs, bump=True)
        return tab

    def import_legacy_tabs(self, tabs):
        with self.lock:
            if self._meta('legacy_tabs', '') != 'pending':
                return False
            if tabs:
                self.save_tabs(self.tabs() + [t for t in tabs if isinstance(t, dict)], bump=True)
            self._set_meta('legacy_tabs', 'done')
        return True

    def mark_legacy_tabs(self):
        with self.lock:
            self._set_meta('legacy_tabs', 'pending')


def migrate_legacy(source, target):
    """Copy a 0.1 history.sqlite3 (one JSON blob per row) into a new project file."""
    old = sqlite3.connect(str(source))
    try:
        rows = old.execute('SELECT id, data FROM history ORDER BY id').fetchall()
    finally:
        old.close()
    project = Project(target)
    for _, data in rows:
        try:
            project.save(json.loads(data))
        except (ValueError, TypeError):
            continue
    project.mark_legacy_tabs()
    return project
