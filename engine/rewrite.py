"""Response decompression and match-and-replace rules, applied to parsed messages (first line, header list, body bytes)."""
import re
import zlib

MAX_DECODED = 8 * 1024 * 1024


def decode_body(headers, body):
    """Decode gzip or deflate bodies so rules, search and the message views see plain bytes.

    Returns (headers, body, note). The Content-Encoding header is removed when the body is decoded; other codings
    such as br stay as received and are named in note."""
    coding = ', '.join(v for k, v in headers if k.lower() == 'content-encoding').strip().lower()
    if not coding or coding == 'identity' or not body:
        return headers, body, ''
    codings = [c.strip() for c in coding.split(',') if c.strip() and c.strip() != 'identity']
    decoded = body
    try:
        for name in reversed(codings):
            if name in ('gzip', 'x-gzip'):
                engine = zlib.decompressobj(16 + zlib.MAX_WBITS)
            elif name == 'deflate':
                # Servers send deflate both zlib-wrapped and raw; try the standard form first.
                engine = zlib.decompressobj(zlib.MAX_WBITS if decoded[:1] == b'\x78' else -zlib.MAX_WBITS)
            else:
                return headers, body, name + ' (not decoded)'
            decoded = engine.decompress(decoded, MAX_DECODED + 1)
            if len(decoded) > MAX_DECODED or engine.unconsumed_tail:
                return headers, body, coding + ' (too large to decode)'
    except zlib.error:
        return headers, body, coding + ' (could not decode)'
    kept = [(k, v) for k, v in headers if k.lower() not in ('content-encoding', 'content-length')]
    return kept, decoded, coding + ' decoded'


def expand(template, match):
    """Replacement text with $1-style group references; $$ is a literal dollar sign."""
    return re.sub(r'\$(\$|\d+)', lambda m: '$' if m.group(1) == '$' else (match.group(int(m.group(1))) or ''), template)


def replace_text(rule, text):
    if rule['regex']:
        return re.sub(rule['match'], lambda m: expand(rule['replace'], m), text)
    return text.replace(rule['match'], rule['replace'])


def replace_bytes(rule, data):
    if rule['regex']:
        pattern = re.compile(rule['match'].encode('utf-8'))
        return pattern.sub(lambda m: expand(rule['replace'], _TextMatch(m)).encode('utf-8'), data)
    return data.replace(rule['match'].encode('utf-8'), rule['replace'].encode('utf-8'))


class _TextMatch:
    """Gives byte-regex groups to expand() as text."""
    def __init__(self, match):
        self.match = match

    def group(self, index):
        value = self.match.group(index)
        return value.decode('utf-8', 'replace') if value is not None else None


def apply_rules(rules, stage, first, headers, body, in_scope):
    """Apply enabled rules for 'request' or 'response'. Returns (first, headers, body, changed)."""
    changed = False
    for rule in rules:
        if not rule.get('enabled') or not rule['target'].startswith(stage + '_') or not (in_scope or rule.get('all_traffic')):
            continue
        part = rule['target'].split('_', 1)[1]
        if part == 'line':
            new = replace_text(rule, first)
            if new != first and new.strip():
                first, changed = new, True
        elif part == 'header':
            lines = [k + ': ' + v for k, v in headers]
            if not rule['match']:
                lines.append(rule['replace'])
                updated = lines
            else:
                updated = [replace_text(rule, line) for line in lines]
            rebuilt = []
            for line in updated:
                if not line.strip():
                    continue  # A header replaced with nothing is removed.
                name, sep, value = line.partition(':')
                if not sep or not name.strip() or any(c.isspace() for c in name.strip()):
                    raise ValueError('Rule "' + (rule.get('comment') or rule['match']) + '" produced an invalid header: ' + line)
                rebuilt.append((name.strip(), value.strip()))
            if rebuilt != headers:
                headers, changed = rebuilt, True
        else:
            new = replace_bytes(rule, body)
            if new != body:
                body, changed = new, True
    return first, headers, body, changed
