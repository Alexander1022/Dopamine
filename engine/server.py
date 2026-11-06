#!/usr/bin/env python3
"""Dopamine: local HTTP and HTTPS testing engine (stdlib only)."""
import argparse
import base64
import fnmatch
import hashlib
import html
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dopamine_mcp import MCPService, PROTOCOLS, VERSION, default_data_dir, rpc_error
from project import EXTENSION, Project, migrate_legacy
from rewrite import apply_rules, decode_body
from tls_ca import CertificateAuthority, normalize_host

# UI files sit beside the engine in the app bundle and in ../ui in the source tree.
UI_DIR = next((d for d in (Path(__file__).parent, Path(__file__).parent.parent / 'ui') if (d / 'index.html').exists()), Path(__file__).parent)

MAX_BODY = 8 * 1024 * 1024
TIMEOUT = 30
HOP = {'connection', 'proxy-connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade', 'proxy-authorization', 'proxy-authenticate'}
# Requests through the proxy to this host are answered by Dopamine itself (CA download for browsers with their own store).
MAGIC_HOST = 'dopamine'
# TLS alerts that mean the client refused the Dopamine certificate, as opposed to an aborted connection.
REJECTION_REASONS = ('CERTIFICATE_UNKNOWN', 'UNKNOWN_CA', 'BAD_CERTIFICATE', 'CERTIFICATE_REVOKED', 'ACCESS_DENIED')
DEFAULT_SETTINGS = dict(tls_intercept=True, verify_upstream=True, passthrough=[], strip_accept_encoding=True, last_project='', recent_projects=[])
DOCUMENTS_PROJECTS = Path.home() / 'Documents' / 'Dopamine Projects'
UI_FILES = ('/app.js', '/style.css', '/curl-import.js', '/design.css', '/mcp-settings.js', '/wire-view.js', '/tls-settings.js',
            '/panel-resize.js', '/guide.js', '/project-tools.js')


class HeldMessages(ValueError):
    """Switching projects with held Intercept messages needs a decision: forward or drop them."""
    def __init__(self, count):
        super().__init__(f'{count} held {"message" if count == 1 else "messages"} must be forwarded or dropped before switching projects.')
        self.count = count


def text_bytes(data):
    return data.decode('utf-8', errors='replace')


def raw_message(first, headers, body):
    return first + '\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n' + text_bytes(body)


def parse_request(raw):
    if '\r\n\r\n' in raw:
        head, sep, body = raw.partition('\r\n\r\n')
    else:
        head, sep, body = raw.partition('\n\n')
    lines = head.replace('\r\n', '\n').split('\n')
    parts = lines.pop(0).split()
    if len(parts) != 3 or not parts[2].startswith('HTTP/'):
        raise ValueError('Request must begin with METHOD /path HTTP/1.1')
    method, target, _ = parts
    if not method.isalpha() or any(ord(c) < 33 for c in target):
        raise ValueError('Invalid request method or target')
    headers = []
    for line in lines:
        if ':' not in line:
            raise ValueError('Each header must contain a colon')
        k, v = line.split(':', 1)
        if not k or any(c.isspace() for c in k) or '\r' in v or '\x00' in v:
            raise ValueError('Invalid request header')
        headers.append((k, v.strip()))
    return method.upper(), target, headers, body.encode('utf-8') if sep else b''


def parse_response(raw):
    # Reuse request header parsing while retaining status and body semantics.
    head = raw.replace('\r\n', '\n').split('\n', 1)[0]
    parts = head.split(' ', 2)
    if len(parts) < 2 or parts[0] not in ('HTTP/1.0', 'HTTP/1.1'):
        raise ValueError('Response must begin with HTTP/1.1 STATUS Reason')
    if not parts[1].isdigit() or not 200 <= int(parts[1]) <= 599:
        raise ValueError('Response status must be between 200 and 599')
    reason = parts[2] if len(parts) > 2 else ''
    if any(ord(c) < 32 for c in reason):
        raise ValueError('Invalid response reason')
    first, separator, remainder = raw.partition('\n')
    ending = '\r\n' if first.endswith('\r') else '\n'
    _, _, headers, body = parse_request('GET / HTTP/1.1' + (ending + remainder if separator else ''))
    if len(body) > MAX_BODY:
        raise ValueError('Response exceeds 8 MiB limit')
    return int(parts[1]), reason, headers, body


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None:
        raise ValueError('Target URL must be http:// or https:// without embedded credentials')
    if any(ord(c) < 33 for c in url):
        raise ValueError('Invalid target URL')
    parsed.port  # Validate malformed port before resolving a held message.
    return parsed


class Engine:
    def __init__(self, data_dir, proxy_port=18080, projects_dir=None):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir)
        self.projects_dir = Path(projects_dir) if projects_dir else self.data_dir / 'Projects'
        self.lock = threading.RLock()
        self.proxy = None
        self.proxy_port = proxy_port
        # A fresh secret per launch, required on /api; the app passes its own on stdin.
        self.api_token = secrets.token_urlsafe(32)
        self.api_port = None
        self.intercept = False
        self.pending = {}
        self.tunnels = set()
        self.closed = False
        self.ca = CertificateAuthority(data_dir)
        self.settings_path = Path(data_dir) / 'settings.json'
        self.settings = dict(DEFAULT_SETTINGS)
        if self.settings_path.exists():
            try:
                saved = json.loads(self.settings_path.read_text())
                self.settings.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS and type(v) is type(DEFAULT_SETTINGS[k])})
            except (ValueError, OSError):
                pass
        self.tls_rejection = None
        self.project = self.startup_project()
        self.mcp = MCPService(self, data_dir)

    @property
    def db(self):
        return self.project.db

    def write_settings(self):
        temp = self.settings_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.settings, indent=2))
        temp.replace(self.settings_path)

    # Projects

    def startup_project(self):
        """The last project, or Default.dopamine. A 0.1 history.sqlite3 in the data folder is migrated into Default once."""
        last = Path(self.settings['last_project']) if self.settings['last_project'] else None
        if last and last.exists():
            try:
                return self.remember(Project(last))
            except (sqlite3.Error, ValueError) as exc:
                print('Could not open ' + str(last) + ': ' + str(exc), file=sys.stderr, flush=True)
        default = self.projects_dir / ('Default' + EXTENSION)
        legacy = self.data_dir / 'history.sqlite3'
        if not default.exists() and legacy.exists():
            project = migrate_legacy(legacy, default)
            legacy.rename(legacy.with_name('history.sqlite3.migrated'))
            return self.remember(project)
        return self.remember(Project(default))

    def remember(self, project):
        path = str(project.path)
        self.settings['last_project'] = path
        self.settings['recent_projects'] = ([path] + [p for p in self.settings['recent_projects'] if p != path])[:10]
        self.write_settings()
        return project

    def project_info(self):
        with self.lock:
            info = self.project.info()
        info['recent'] = [dict(name=Path(p).stem, path=p) for p in self.settings['recent_projects'] if Path(p).exists()]
        info['projects_dir'] = str(self.projects_dir)
        return info

    def new_project_path(self):
        stamp = time.strftime('%Y-%m-%d %H-%M')
        for n in range(1, 1000):
            path = self.projects_dir / ('Untitled ' + stamp + ('' if n == 1 else f' {n}') + EXTENSION)
            if not path.exists():
                return path
        raise ValueError('Could not choose a name for the new project')

    def _switch(self, held):
        held_count = sum(p['action'] is None for p in self.pending.values())
        if held_count and held not in ('forward', 'drop'):
            raise HeldMessages(held_count)
        if held_count:
            self.release_pending(held)

    def open_project(self, path=None, create=False, held=None):
        """Open (or create) a project and make it current. Held messages are forwarded or dropped first."""
        target = Path(path).expanduser() if path else self.new_project_path()
        if target.suffix != EXTENSION:
            target = target.with_name(target.name + EXTENSION)
        if create and target.exists():
            raise ValueError('A project named ' + target.name + ' already exists')
        if not create and not target.exists():
            raise KeyError('Project file not found: ' + str(target))
        with self.lock:
            if target.resolve() == self.project.path.resolve():
                return self.project_info()
            self._switch(held)
            new = Project(target)
            old, self.project = self.project, new
            old.close()
            self.remember(new)
        return self.project_info()

    def move_project(self, path):
        """Rename or move the current project file; it stays open."""
        target = Path(path).expanduser()
        if target.suffix != EXTENSION:
            target = target.with_name(target.name + EXTENSION)
        with self.lock:
            source = self.project.path
            if target.resolve() == source.resolve():
                return self.project_info()
            if target.exists():
                raise ValueError('A file named ' + target.name + ' already exists there')
            target.parent.mkdir(parents=True, exist_ok=True)
            self.project.close()
            try:
                shutil.move(str(source), str(target))
            except OSError:
                self.project = Project(source)
                raise
            self.project = Project(target)
            self.settings['recent_projects'] = [p for p in self.settings['recent_projects'] if p != str(source)]
            self.remember(self.project)
        return self.project_info()

    def in_scope(self, url):
        with self.lock:
            return self.project.in_scope(url)

    def traffic_settings(self):
        return dict(strip_accept_encoding=self.settings['strip_accept_encoding'])

    def configure_traffic(self, data):
        if set(data) - {'strip_accept_encoding'} or not isinstance(data.get('strip_accept_encoding', True), bool):
            raise ValueError('strip_accept_encoding must be true or false')
        with self.lock:
            self.settings.update(data)
            self.write_settings()
        return self.traffic_settings()

    @property
    def tls_intercept(self):
        return self.settings['tls_intercept']

    def tls_settings(self, check_trust=False):
        with self.lock:
            result = dict(intercept=self.settings['tls_intercept'], verify_upstream=self.settings['verify_upstream'],
                          passthrough=list(self.settings['passthrough']), ca=self.ca.info(), rejection=self.tls_rejection)
        if check_trust:
            result['ca']['trusted'] = self.ca.trusted()
        return result

    def configure_tls(self, data):
        changes = {}
        for key, name in (('intercept', 'tls_intercept'), ('verify_upstream', 'verify_upstream')):
            if key in data:
                if not isinstance(data[key], bool):
                    raise ValueError(key + ' must be true or false')
                changes[name] = data[key]
        if 'passthrough' in data:
            hosts = data['passthrough']
            if not isinstance(hosts, list) or len(hosts) > 500 or not all(isinstance(h, str) for h in hosts):
                raise ValueError('passthrough must be a list of host patterns')
            cleaned = []
            for host in hosts:
                host = host.strip().lower()
                if host and host not in cleaned:
                    if len(host) > 253 or any(c.isspace() or c in '/:@' for c in host):
                        raise ValueError('Invalid pass-through host pattern: ' + host)
                    cleaned.append(host)
            changes['passthrough'] = cleaned
        with self.lock:
            self.settings.update(changes)
            if changes.get('tls_intercept'):
                self.ca.ensure()
            self.write_settings()
        return self.tls_settings()

    def passthrough(self, host):
        host = host.lower().rstrip('.')
        return any(fnmatch.fnmatchcase(host, pattern) or (pattern.startswith('*.') and host == pattern[2:])
                   for pattern in self.settings['passthrough'])

    def upstream_context(self):
        if self.settings['verify_upstream']:
            return ssl.create_default_context()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def save(self, item):
        with self.lock:
            if self.closed:
                return dict(item, id=None)
            # Out-of-scope proxy traffic is still forwarded; the project's scope setting decides whether it is recorded.
            if item.get('source', 'proxy') == 'proxy' and not self.project.scope['record_out_of_scope'] and not self.project.in_scope(item.get('url', '')):
                return dict(item, id=None)
            item['id'] = self.project.save(item)
        return item

    def history(self, ident=None, **filters):
        with self.lock:
            if ident is not None:
                return self.project.get(ident)
            return self.project.query(limit=filters.pop('limit', 2000), **filters)[0]

    def status(self):
        with self.lock:
            info = self.project.info()
            return dict(app='dopamine', version=VERSION, proxy_running=self.proxy is not None, proxy_port=self.proxy_port, intercept=self.intercept,
                        tls_intercept=self.settings['tls_intercept'], tls_rejection=self.tls_rejection, history_count=info['history_count'],
                        pending_count=sum(p['action'] is None for p in self.pending.values()),
                        project=dict(name=info['name'], path=info['path'], revision=info['revision'], legacy_tabs=info['legacy_tabs']),
                        scope_active=bool(self.project.scope['include'] or self.project.scope['exclude']))

    def set_proxy(self, running, port=None):
        if port is not None and not 1024 <= int(port) <= 65535:
            raise ValueError('Proxy port must be between 1024 and 65535')
        if not running:
            with self.lock:
                old, self.proxy = self.proxy, None
                self.release_pending()
                for connection in list(self.tunnels):
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    connection.close()
            if old:
                old.shutdown()
                old.server_close()
        else:
            with self.lock:
                if self.proxy is not None:
                    return self.status()
                new_port = int(port or self.proxy_port)
                server = LocalServer(('127.0.0.1', new_port), ProxyHandler)
                server.engine = self
                self.proxy, self.proxy_port = server, new_port
                threading.Thread(target=server.serve_forever, daemon=True).start()
        return self.status()

    def release_pending(self, action='drop'):
        with self.lock:
            for p in self.pending.values():
                if p['action'] is None:
                    p['action'] = action
                    p['event'].set()

    def bulk_resolve(self, action, edits=None):
        if action not in ('forward', 'drop'):
            raise ValueError('Unknown interception action')
        if edits is None:
            edits = {}
        if not isinstance(edits, dict):
            raise ValueError('Bulk edits must be an object')
        allowed = {'request', 'url', 'intercept_response', 'response'}
        with self.lock:
            ids = [ident for ident, p in self.pending.items() if p['action'] is None]
            for ident, edit in edits.items():
                if ident not in ids:
                    raise KeyError('Pending message no longer exists')
                if not isinstance(edit, dict) or set(edit) - allowed:
                    raise ValueError('Invalid bulk message edit')
            for ident in ids:
                self.resolve(ident, action, validate_only=True, **edits.get(ident, {}))
            for ident in ids:
                self.resolve(ident, action, **edits.get(ident, {}))
        return {'ok': True, 'count': len(ids)}

    def _hold(self, url, raw, stage='request', response=None, require_proxy=False):
        with self.lock:
            if (require_proxy or stage == 'response') and self.proxy is None:
                raise ValueError('Proxy stopped')
            if not self.intercept or (stage == 'request' and not self.project.in_scope(url)):
                return dict(url=url, request=raw, response=response, intercept_response=False)
            ident = uuid.uuid4().hex
            p = dict(id=ident, stage=stage, method=raw.split(' ', 1)[0], url=url,
                     request=raw, time=time.time(), event=threading.Event(), action=None,
                     intercept_response=False)
            if response is not None:
                p['response'] = response
            self.pending[ident] = p
        signaled = p['event'].wait(300)
        with self.lock:
            self.pending.pop(ident, None)
        if not signaled or p['action'] != 'forward':
            raise ValueError(stage.capitalize() + ' dropped or interception timed out')
        return p

    def intercept_request(self, url, raw):
        return self._hold(url, raw)['request']

    def resolve(self, ident, action, request=None, url=None, intercept_response=None, response=None, validate_only=False):
        if action not in ('forward', 'drop'):
            raise ValueError('Unknown interception action')
        with self.lock:
            p = self.pending.get(ident)
            if p is None or p['action'] is not None:
                raise KeyError('Pending message no longer exists')
            # Validate all edits before changing any state, so a bad edit can be retried.
            if action == 'forward':
                if p['stage'] == 'request':
                    if request is not None:
                        _, _, _, body = parse_request(request)
                        if len(body) > MAX_BODY:
                            raise ValueError('Request exceeds 8 MiB limit')
                    if url is not None:
                        validate_url(url)
                    if intercept_response is not None and not isinstance(intercept_response, bool):
                        raise ValueError('intercept_response must be true or false')
                    if validate_only:
                        return {'ok': True}
                    if request is not None:
                        p['request'] = request
                    if url is not None:
                        p['url'] = url
                    if intercept_response is not None:
                        p['intercept_response'] = intercept_response
                elif response is not None:
                    parse_response(response)
                    if validate_only:
                        return {'ok': True}
                    p['response'] = response
            if not validate_only:
                p['action'] = action
                p['event'].set()
        return {'ok': True}

    def targets_self(self, parsed):
        """Whether a URL reaches this engine's API or proxy listener on a local address."""
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in {self.api_port, self.proxy_port}:
            return False
        try:
            infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
        except OSError:
            return False
        addresses = [ipaddress.ip_address(info[4][0].split('%')[0]) for info in infos]
        # ::ffff:127.0.0.1 connects to 127.0.0.1 but is not is_loopback before Python 3.13.
        addresses = [getattr(a, 'ipv4_mapped', None) or a for a in addresses]
        return any(a.is_loopback or a.is_unspecified for a in addresses)

    def exchange(self, url, raw, source='repeater', body_override=None, intercept_response=False, original_request=None):
        started = time.monotonic()
        item = dict(method='', url=url, status=0, length=0, duration_ms=0, source=source, time=time.time(), error='', tunnel=False, request=raw, response='')
        if original_request is not None:
            item.update(original_request=original_request, rewritten=True)
        connection = None
        response_headers, response_body = [], b''
        try:
            method, target, headers, body = parse_request(raw)
            if body_override is not None:
                body = body_override
            item['method'] = method
            parsed = validate_url(url)
            if method == 'CONNECT':
                raise ValueError('Use the proxy for CONNECT tunnels')
            if len(body) > MAX_BODY:
                raise ValueError('Request exceeds 8 MiB limit')
            if target.startswith(('http://', 'https://')):
                path_parts = urllib.parse.urlsplit(target)
                target = urllib.parse.urlunsplit(('', '', path_parts.path or '/', path_parts.query, ''))
            if not target.startswith('/') and target != '*':
                raise ValueError('Request target must be an absolute path')
            if parsed.scheme == 'https':
                connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=TIMEOUT, context=self.upstream_context())
            else:
                connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=TIMEOUT)
            blocked = HOP | {'content-length'}
            for k, v in headers:
                if k.lower() == 'connection':
                    blocked.update(x.strip().lower() for x in v.split(','))
            headers = [(k, v) for k, v in headers if k.lower() not in blocked]
            if not any(k.lower() == 'host' for k, _ in headers):
                headers.insert(0, ('Host', parsed.netloc))
            if body or method in ('POST', 'PUT', 'PATCH'):
                headers.append(('Content-Length', str(len(body))))
            headers.append(('Connection', 'close'))
            item['request'] = raw_message(f'{method} {target} HTTP/1.1', headers, body)
            item['request_body_base64'] = base64.b64encode(body).decode()
            item['url'] = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, target.split('?', 1)[0], target.split('?', 1)[1] if '?' in target else '', ''))
            connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            for k, v in headers:
                connection.putheader(k, v)
            connection.endheaders(body)
            res = connection.getresponse()
            response_headers = res.getheaders()
            response_body = res.read(MAX_BODY + 1)
            if len(response_body) > MAX_BODY:
                raise ValueError('Response exceeds 8 MiB limit')
            status, reason = res.status, res.reason
            # gzip and deflate are decoded so rules, search and the views see the body; the client receives it decoded.
            response_headers, response_body, item['encoding'] = decode_body(response_headers, response_body)
            if source == 'proxy':
                with self.lock:
                    rules, in_scope = list(self.project.rules), self.project.in_scope(item['url'])
                before = raw_message(f'HTTP/1.1 {status} {reason}', response_headers, response_body)
                first, new_headers, new_body, changed = apply_rules(rules, 'response', f'HTTP/1.1 {status} {reason}', response_headers, response_body, in_scope)
                if changed:
                    parts = first.split(' ', 2)
                    if len(parts) < 2 or not parts[1].isdigit() or not 100 <= int(parts[1]) <= 599:
                        raise ValueError('A response rule produced an invalid status line: ' + first)
                    status, reason, response_headers, response_body = int(parts[1]), parts[2] if len(parts) > 2 else '', new_headers, new_body
                    item.update(original_response=before, rewritten=True)
            if item['encoding'] or item.get('original_response'):
                response_headers = [(k, v) for k, v in response_headers if k.lower() not in ('content-length', 'transfer-encoding')]
                if not (status in (204, 304) or method == 'HEAD'):
                    response_headers.append(('Content-Length', str(len(response_body))))
            original_response = raw_message(f'HTTP/1.1 {status} {reason}', response_headers, response_body)
            if source == 'proxy' and intercept_response:
                held = self._hold(item['url'], item['request'], 'response', original_response)
                if held['response'] != original_response:
                    status, reason, response_headers, response_body = parse_response(held['response'])
                    # Bodyless statuses must never send a payload to the client.
                    if status in (204, 304) or method == 'HEAD':
                        response_body = b''
                    response_headers = [(k, v) for k, v in response_headers if k.lower() not in ('content-length', 'transfer-encoding')]
                    response_headers.append(('Content-Length', str(len(response_body))))
            item.update(status=status, length=len(response_body), response=raw_message(f'HTTP/1.1 {status} {reason}', response_headers, response_body), response_body_base64=base64.b64encode(response_body).decode())
        except Exception as exc:
            response_headers, response_body = [], b''
            item['status'] = 0
            item['error'] = str(exc)
            item['response'] = 'Request failed: ' + str(exc)
        finally:
            if connection:
                connection.close()
        item['duration_ms'] = round((time.monotonic() - started) * 1000)
        return self.save(item), response_headers, response_body


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(TIMEOUT)

    def log_message(self, *_):
        pass

    def body(self):
        encoding = self.headers.get('Transfer-Encoding', '').lower()
        if encoding:
            if encoding != 'chunked':
                raise ValueError('Unsupported transfer encoding')
            data = bytearray()
            while True:
                line = self.rfile.readline(128)
                size = int(line.split(b';')[0].strip(), 16)
                if size < 0 or len(data) + size > MAX_BODY:
                    raise ValueError('Request exceeds 8 MiB limit')
                if size == 0:
                    while True:
                        trailer = self.rfile.readline(8192)
                        if trailer in (b'\r\n', b'\n', b''):
                            break
                    break
                part = self.rfile.read(size)
                if len(part) != size or self.rfile.read(2) != b'\r\n':
                    raise ValueError('Incomplete chunked body')
                data.extend(part)
            return bytes(data)
        lengths = self.headers.get_all('Content-Length', [])
        if len(set(lengths)) > 1:
            raise ValueError('Conflicting Content-Length headers')
        size = int(lengths[0]) if lengths else 0
        if size < 0 or size > MAX_BODY:
            raise ValueError('Request exceeds 8 MiB limit')
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError('Incomplete request body')
        return data

    def output(self, status, data, content_type='application/json'):
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(data)


class ProxyHandler(QuietHandler):
    origin = None  # Set to https://host[:port] for requests decrypted from a CONNECT tunnel.

    def do_CONNECT(self):
        engine = self.server.engine
        try:
            parsed = urllib.parse.urlsplit('//' + self.path)
            host, port = parsed.hostname, parsed.port
        except ValueError:
            host = port = None
        if host and port and engine.tls_intercept and not engine.passthrough(host):
            return self.intercept_tls(host, port)
        self.tunnel()

    def intercept_tls(self, host, port):
        """Terminate the client's TLS with a Dopamine leaf certificate and handle the decrypted requests."""
        engine = self.server.engine
        try:
            context = engine.ca.context_for(host)
        except Exception as exc:
            self.close_connection = True
            return self.output(502, {'error': 'Could not issue a certificate for ' + host + ': ' + str(exc)})
        self.send_response(200, 'Connection Established')
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        client = context.wrap_socket(self.connection, server_side=True, do_handshake_on_connect=False)
        with engine.lock:
            if engine.proxy is None:
                client.close()
                return
            # Registered before the handshake so stopping the listener also ends connections that never complete it.
            engine.tunnels.add(client)
        try:
            client.do_handshake()
            authority = ('[' + host + ']' if ':' in host else host) + ('' if port == 443 else ':' + str(port))
            TLSProxyHandler(client, self.client_address, self.server, 'https://' + authority)
        except ssl.SSLError as exc:
            reason = str(getattr(exc, 'reason', '') or exc)
            if any(r in reason.upper() for r in REJECTION_REASONS):
                with engine.lock:
                    engine.tls_rejection = dict(host=host, time=time.time(), reason=reason)
                engine.save(dict(method='CONNECT', url=f'https://{self.path}', status=0, length=0, duration_ms=0, source='proxy',
                                 time=time.time(), tunnel=True, request=self.requestline,
                                 error='The client rejected the Dopamine certificate. Trust the Dopamine CA, or add this host to pass-through.',
                                 response='TLS handshake failed: ' + reason))
        except OSError:
            pass
        finally:
            with engine.lock:
                engine.tunnels.discard(client)
            try:
                client.close()
            except OSError:
                pass

    def tunnel(self):
        engine = self.server.engine
        start = time.monotonic()
        remote = None
        error = ''
        status = 200
        try:
            parsed = urllib.parse.urlsplit('//' + self.path)
            if not parsed.hostname or not parsed.port:
                raise ValueError('CONNECT requires host:port')
            remote = socket.create_connection((parsed.hostname, parsed.port), TIMEOUT)
            with engine.lock:
                if engine.proxy is None:
                    raise ValueError('Proxy stopped')
                engine.tunnels.update((self.connection, remote))
            self.send_response(200, 'Connection Established')
            self.end_headers()
            self.wfile.flush()
            self.connection.setblocking(False)
            remote.setblocking(False)
            while True:
                ready, _, _ = select.select([self.connection, remote], [], [], TIMEOUT)
                if not ready:
                    break
                end = False
                for src in ready:
                    data = src.recv(65536)
                    if not data:
                        end = True
                        break
                    dst = remote if src is self.connection else self.connection
                    dst.settimeout(TIMEOUT)
                    dst.sendall(data)
                    dst.setblocking(False)
                if end:
                    break
        except Exception as exc:
            error = str(exc)
            if remote is None:
                status = 502
                self.output(502, {'error': error})
        finally:
            with engine.lock:
                engine.tunnels.discard(self.connection)
                engine.tunnels.discard(remote)
            if remote:
                remote.close()
            self.close_connection = True
            engine.save(dict(method='CONNECT', url='https://' + self.path, status=status, length=0, duration_ms=round((time.monotonic()-start)*1000), source='proxy', time=time.time(), error=error, tunnel=True, request=self.requestline, response='Pass-through tunnel; HTTPS payload is not decrypted.' if engine.tls_intercept else 'Encrypted tunnel; HTTPS interception is off.'))

    def certificate_page(self, path):
        """Answer http://dopamine/ through the proxy: a page and downloads for the Dopamine CA certificate."""
        ca = self.server.engine.ca
        if path in ('/cert', '/cert.pem', '/dopamine-ca.pem'):
            data, kind, name = ca.pem(), 'application/x-pem-file', 'Dopamine-CA.pem'
        elif path in ('/cert.cer', '/cert.der', '/dopamine-ca.cer'):
            data, kind, name = ca.der(), 'application/x-x509-ca-cert', 'Dopamine-CA.cer'
        else:
            info = ca.info() if ca.exists() else {}
            page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Dopamine CA</title>'
                    '<body style="font:15px -apple-system,system-ui,sans-serif;max-width:560px;margin:48px auto;padding:0 16px;line-height:1.5">'
                    '<h1 style="font-size:22px">Dopamine certificate</h1><p>Install and trust this certificate to inspect HTTPS traffic '
                    'from this browser or device. Only trust a CA you generated yourself.</p>'
                    '<p><a href="/cert.pem">Download PEM</a> · <a href="/cert.cer">Download DER (.cer)</a></p>'
                    + (f'<p style="color:#666;font-size:13px">{html.escape(info.get("name", ""))}<br>SHA-256 {html.escape(info.get("sha256", ""))}</p>' if info else '')
                    + '<p style="color:#666;font-size:13px">Firefox: Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import.</p>')
            return self.output(200, page, 'text/html; charset=utf-8')
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Disposition', 'attachment; filename="' + name + '"')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(data)

    def forward(self):
        item = None
        url, raw = '', self.requestline
        try:
            body = self.body()
            if self.origin and self.path.startswith('/'):
                url = self.origin + self.path
            elif self.path.startswith(('http://', 'https://')):
                url = self.path
            else:
                url = 'http://' + self.headers.get('Host', '') + self.path
            if not self.origin and urllib.parse.urlsplit(url).hostname == MAGIC_HOST:
                return self.certificate_page(urllib.parse.urlsplit(url).path)
            engine = self.server.engine
            first, headers = self.requestline, list(self.headers.items())
            original = raw_message(first, headers, body)
            # Ask for uncompressed responses so rules and search see plain bodies; gzip and deflate are decoded either way.
            if engine.settings['strip_accept_encoding']:
                headers = [(k, v) for k, v in headers if k.lower() != 'accept-encoding']
            with engine.lock:
                rules, in_scope = list(engine.project.rules), engine.project.in_scope(url)
            first, headers, body, changed = apply_rules(rules, 'request', first, headers, body, in_scope)
            raw = raw_message(first, headers, body)
            held = engine._hold(url, raw, require_proxy=True)
            edited = held['request']
            item, headers, response = engine.exchange(held['url'], edited, 'proxy', body_override=body if edited == raw else None,
                                                      intercept_response=held['intercept_response'], original_request=original if changed else None)
            if item['error']:
                self.output(502, {'error': item['error']})
                return
            self.send_response_only(item['status'])
            excluded = HOP | {'content-length'}
            for k, v in headers:
                if k.lower() == 'connection':
                    excluded.update(x.strip().lower() for x in v.split(','))
            for k, v in headers:
                if k.lower() not in excluded:
                    self.send_header(k, v)
            declared_length = next((v for k, v in headers if k.lower() == 'content-length'), str(len(response)))
            self.send_header('Content-Length', declared_length if self.command == 'HEAD' else str(len(response)))
            # Decrypted connections stay open between requests so browsers avoid a TLS handshake per request.
            keep = (self.origin is not None and self.request_version == 'HTTP/1.1'
                    and 'close' not in self.headers.get('Connection', '').lower() and self.server.engine.proxy is not None)
            self.send_header('Connection', 'keep-alive' if keep else 'close')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(response)
            self.close_connection = not keep
        except Exception as exc:
            if item is None:
                self.server.engine.save(dict(method=self.command, url=url, status=0, length=0,
                    duration_ms=0, source='proxy', time=time.time(), error=str(exc), tunnel=False,
                    request=raw, response='Request failed: ' + str(exc)))
            self.output(502, {'error': str(exc)})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = forward


class TLSProxyHandler(ProxyHandler):
    """Serves requests on a client connection that Dopamine has decrypted."""
    def __init__(self, request, client_address, server, origin):
        self.origin = origin
        super().__init__(request, client_address, server)

    def do_CONNECT(self):
        self.output(400, {'error': 'CONNECT inside a decrypted tunnel is not supported'})


def system_certificate_action(engine, action):
    """Reveal the CA in Finder, or add/remove it as a trusted root in the login keychain (macOS asks for approval)."""
    engine.ca.ensure()
    path = str(engine.ca.ca_cert)
    if action == 'reveal':
        subprocess.run(['/usr/bin/open', '-R', path], timeout=10, check=True)
        return {'ok': True}
    keychain = str(Path.home() / 'Library' / 'Keychains' / 'login.keychain-db')
    if action == 'trust':
        command = ['/usr/bin/security', 'add-trusted-cert', '-r', 'trustRoot', '-p', 'ssl', '-p', 'basic', '-k', keychain, path]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    else:
        sha1 = hashlib.sha1(engine.ca.der()).hexdigest().upper()
        subprocess.run(['/usr/bin/security', 'remove-trusted-cert', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        result = subprocess.run(['/usr/bin/security', 'delete-certificate', '-Z', sha1, keychain], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode != 0:
        detail = result.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise ValueError('macOS did not change certificate trust' + (': ' + detail[-1] if detail else '.'))
    return engine.tls_settings(check_trust=True)


def history_page(engine, params):
    """GET /api/history: newest first, filtered and searched in the project database."""
    flag = lambda key: params.get(key) in ('1', 'true')
    number = lambda key: int(params[key]) if params.get(key, '').lstrip('-').isdigit() else None
    status = number('status')
    with engine.lock:
        items, total, more = engine.project.query(
            query=params.get('q', ''), method=params.get('method') or None, source=params.get('source') or None, status=status,
            in_scope=flag('scope'), search=params.get('search') or None, search_in=params.get('in', 'both'), regex=flag('regex'),
            case_sensitive=flag('case'), before_id=number('before_id'), limit=max(1, min(number('limit') or 500, 5000)))
        count = engine.project.count()
    return dict(items=items, total_count=total, has_more=more, history_count=count)


API_TOKEN_ERROR = {'error': 'Dopamine API token required. Reopen Dopamine, or open the URL the engine printed at startup.'}


class APIHandler(QuietHandler):
    def trusted(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{h}' for h in hosts}:
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return False
        return True

    def token_matches(self, value):
        return bool(value) and secrets.compare_digest(value.encode('utf-8'), self.server.engine.api_token.encode('utf-8'))

    def api_authorized(self):
        # Header only: a cookie would be scoped to the host, not the port, and reach other 127.0.0.1 servers.
        return self.token_matches(self.headers.get('X-Dopamine-Token'))

    def do_GET(self):
        if not self.trusted():
            return self.output(403, {'error': 'Local origin required'})
        split = urllib.parse.urlsplit(self.path)
        path, params = split.path, dict(urllib.parse.parse_qsl(split.query))
        if path.startswith('/api/') and not self.api_authorized():
            return self.output(401, API_TOKEN_ERROR)
        engine = self.server.engine
        try:
            if path == '/mcp':
                if not self.mcp_access():
                    return
                return self.output(405, {'error': 'Use POST for stateless MCP. SSE is not offered.'})
            if path == '/api/mcp/settings':
                return self.output(200, engine.mcp.settings(self.server.server_port))
            if path == '/api/mcp/history':
                return self.output(200, engine.mcp.history(before_id=params.get('before_id'), limit=params.get('limit', 50), query=params.get('q', ''),
                                                           kind=params.get('kind', ''), client=params.get('client', '')))
            if path == '/api/status':
                return self.output(200, engine.status())
            if path == '/api/tls':
                return self.output(200, engine.tls_settings(check_trust=True))
            if path in ('/api/tls/ca.pem', '/api/tls/ca.cer'):
                pem = path.endswith('.pem')
                data = engine.ca.pem() if pem else engine.ca.der()
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-pem-file' if pem else 'application/x-x509-ca-cert')
                self.send_header('Content-Disposition', 'attachment; filename="Dopamine-CA.' + ('pem"' if pem else 'cer"'))
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                self.wfile.write(data)
                return
            if path == '/api/history':
                return self.output(200, history_page(engine, params))
            if path.startswith('/api/history/'):
                return self.output(200, engine.history(int(path.rsplit('/', 1)[1])))
            if path == '/api/project':
                return self.output(200, engine.project_info())
            if path == '/api/scope':
                with engine.lock:
                    return self.output(200, engine.project.scope)
            if path == '/api/rules':
                with engine.lock:
                    return self.output(200, {'rules': engine.project.rules})
            if path == '/api/repeater/tabs':
                with engine.lock:
                    return self.output(200, {'tabs': engine.project.tabs(), 'revision': engine.project.revision})
            if path == '/api/traffic':
                return self.output(200, engine.traffic_settings())
            if path == '/api/intercept':
                with engine.lock:
                    items = [{k: v for k, v in p.items() if k not in ('event', 'action')} for p in engine.pending.values() if p['action'] is None]
                return self.output(200, {'items': items})
            if path in ('/', '/index.html'):
                return self.output(200, (UI_DIR / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if path in UI_FILES:
                return self.output(200, (UI_DIR / path[1:]).read_bytes(), 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
            return self.output(404, {'error': 'Not found'})
        except KeyError as exc:
            self.output(404, {'error': str(exc)})
        except Exception as exc:
            self.output(400, {'error': str(exc)})

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path == '/mcp':
            return self.mcp_post()
        if not self.trusted() or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.output(403, {'error': 'Local origin and application/json required'})
        if not self.api_authorized():
            return self.output(401, API_TOKEN_ERROR)
        engine = self.server.engine
        try:
            data = json.loads(self.body() or b'{}')
            path = urllib.parse.urlsplit(self.path).path
            if path == '/api/mcp/settings':
                engine.mcp.configure(data)
                result = engine.mcp.settings(self.server.server_port)
            elif path == '/api/mcp/history/clear':
                engine.mcp.clear_history()
                result = {'ok': True}
            elif path == '/api/tls':
                result = engine.configure_tls(data)
            elif path == '/api/tls/regenerate':
                engine.ca.generate()
                with engine.lock:
                    engine.tls_rejection = None
                result = engine.tls_settings(check_trust=True)
            elif path in ('/api/tls/trust', '/api/tls/untrust', '/api/tls/reveal'):
                result = system_certificate_action(engine, path.rsplit('/', 1)[1])
            elif path == '/api/proxy':
                result = engine.set_proxy(bool(data.get('running')), data.get('port'))
            elif path == '/api/intercept':
                with engine.lock:
                    enabled = bool(data.get('enabled'))
                    if not enabled:
                        engine.bulk_resolve('forward', data.get('edits'))
                    engine.intercept = enabled
                result = engine.status()
            elif path == '/api/intercept/bulk':
                result = engine.bulk_resolve(data.get('action'), data.get('edits'))
            elif path.startswith('/api/intercept/'):
                _, _, _, ident, action = path.split('/')
                if action not in ('forward', 'drop'):
                    raise ValueError('Unknown interception action')
                result = engine.resolve(ident, action, request=data.get('request'), url=data.get('url'), intercept_response=data.get('intercept_response'), response=data.get('response'))
            elif path == '/api/repeater':
                result, _, _ = engine.exchange(data.get('url', ''), data.get('request', ''))
            elif path == '/api/history/clear':
                with engine.lock:
                    engine.project.clear()
                result = {'ok': True}
            elif path.startswith('/api/history/') and path.endswith('/annotate'):
                with engine.lock:
                    result = engine.project.annotate(int(path.split('/')[3]), data.get('color'), data.get('note'))
            elif path == '/api/project':
                action = data.get('action')
                if action == 'new':
                    result = engine.open_project(data.get('path'), create=True, held=data.get('held'))
                elif action == 'open':
                    result = engine.open_project(data.get('path'), held=data.get('held'))
                elif action == 'move':
                    result = engine.move_project(data.get('path') or '')
                else:
                    raise ValueError('action must be new, open or move')
            elif path == '/api/scope':
                with engine.lock:
                    if data.get('add'):
                        add = data['add']
                        result = engine.project.add_to_scope(add.get('host', ''), add.get('path', ''), bool(add.get('exclude')))
                    else:
                        result = engine.project.set_scope(data.get('include'), data.get('exclude'), data.get('record_out_of_scope'))
            elif path == '/api/rules':
                with engine.lock:
                    result = {'rules': engine.project.replace_rules(data.get('rules'))}
            elif path == '/api/repeater/tabs':
                with engine.lock:
                    result = {'revision': engine.project.save_tabs(data.get('tabs'))}
            elif path == '/api/repeater/legacy':
                with engine.lock:
                    result = {'imported': engine.project.import_legacy_tabs(data.get('tabs') or [])}
            elif path == '/api/traffic':
                result = engine.configure_traffic(data)
            else:
                return self.output(404, {'error': 'Not found'})
            self.output(200, result)
        except HeldMessages as exc:
            self.output(409, {'error': str(exc), 'held': exc.count})
        except KeyError as exc:
            self.output(404, {'error': str(exc)})
        except Exception as exc:
            self.output(400, {'error': str(exc)})

    def mcp_access(self):
        if not self.trusted():
            self.output(403, {'error': 'Local origin required'})
            return False
        if not self.server.engine.mcp.authorized(self.headers.get('Authorization')):
            self.output(401, {'error': 'MCP bearer token required. Use the bundled stdio bridge.'})
            return False
        if not self.server.engine.mcp.enabled:
            self.output(403, {'error': 'Enable Agent access in Dopamine → Agents.'})
            return False
        return True

    def mcp_post(self):
        if not self.mcp_access():
            return
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.output(415, {'error': 'Use application/json'})
        accept = self.headers.get('Accept', '')
        if 'application/json' not in accept or 'text/event-stream' not in accept:
            return self.output(406, {'error': 'Accept must include application/json and text/event-stream'})
        if self.headers.get('MCP-Protocol-Version', '2025-03-26') not in PROTOCOLS:
            return self.output(400, {'error': 'Unsupported MCP-Protocol-Version'})
        try:
            message = json.loads(self.body(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
        except (ValueError, UnicodeError):
            return self.output(400, rpc_error(None, -32700, 'Invalid JSON or message exceeds 8 MiB.'))
        # No server-initiated requests are issued, so unsolicited responses need no reply.
        if isinstance(message, dict) and 'method' not in message and ('result' in message or 'error' in message):
            return self.output(202, b'')
        client = (self.headers.get('X-Dopamine-Client') or '').strip()[:80]
        response = self.server.engine.mcp.dispatch(message, client)
        return self.output(202, b'') if response is None else self.output(200, response)

    def do_DELETE(self):
        if urllib.parse.urlsplit(self.path).path == '/mcp' and self.mcp_access():
            return self.output(405, {'error': 'Stateless MCP has no session to delete.'})
        if urllib.parse.urlsplit(self.path).path != '/mcp':
            self.output(404, {'error': 'Not found'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=int(os.environ.get('DOPAMINE_API_PORT', '18765')))
    parser.add_argument('--proxy-port', type=int, default=18080)
    parser.add_argument('--data-dir', default=os.environ.get('DOPAMINE_DATA_DIR'), help='data folder (default: ~/Library/Application Support/Dopamine)')
    parser.add_argument('--token-stdin', action='store_true', help='read the API token from the first line of stdin (used by the app)')
    parser.add_argument('--version', action='version', version='Dopamine ' + VERSION)
    parser.add_argument('--projects-dir', default=os.environ.get('DOPAMINE_PROJECTS_DIR'), help='folder for new projects (default: ~/Documents/Dopamine Projects)')
    args = parser.parse_args()
    data_dir = Path(args.data_dir or default_data_dir())
    # A separate data folder (a test copy) keeps its projects inside it rather than in Documents.
    projects = args.projects_dir or (DOCUMENTS_PROJECTS if data_dir.resolve() == default_data_dir().resolve() else data_dir / 'Projects')
    engine = Engine(str(data_dir), args.proxy_port, projects)
    if args.token_stdin:
        engine.api_token = sys.stdin.readline().strip()
        if len(engine.api_token) < 32:
            parser.error('--token-stdin needs a token of at least 32 characters')
    server = LocalServer(('127.0.0.1', args.port), APIHandler)
    server.engine = engine
    engine.api_port = server.server_port
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    # The app already holds the token; print it only for a browser opened by hand.
    address = f'http://127.0.0.1:{server.server_port}/' + ('' if args.token_stdin else '?token=' + engine.api_token)
    print(f'Dopamine {VERSION} ready at {address}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.set_proxy(False)
        server.server_close()
        with engine.lock:
            engine.closed = True
            engine.project.close()


if __name__ == '__main__':
    main()
