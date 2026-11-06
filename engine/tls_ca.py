"""Dopamine certificate authority: a local CA and per-host leaf certificates for HTTPS interception.

Keys and certificates are created with the system /usr/bin/openssl (LibreSSL on macOS), so the engine stays
dependency-free. The CA private key never leaves the data folder and is readable only by the current user.
"""
import collections
import datetime
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading

OPENSSL = os.environ.get('DOPAMINE_OPENSSL', '/usr/bin/openssl')
SECURITY = '/usr/bin/security'
HOSTNAME = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62}))*\.?\Z')
CONFIG = """[req]
distinguished_name=dn
[dn]
[v3_ca]
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
[v3_leaf]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName={san}
"""
# Browsers reject leaf certificates valid for more than 398 days.
LEAF_DAYS = 397
CA_DAYS = 3650
CONTEXT_CACHE = 512


def normalize_host(host):
    """Return a lowercase host name or IP literal that is safe to place in a certificate, or raise ValueError."""
    host = (host or '').strip().strip('[]').lower().rstrip('.')
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if not HOSTNAME.match(host):
        raise ValueError('Invalid host name for a certificate')
    return host


def subject_value(text):
    # openssl -subj treats '/' and '=' specially; host names and the CA label never contain them after validation.
    return re.sub(r'[/=\\]', '-', text)


class CertificateAuthority:
    def __init__(self, data_dir):
        self.dir = Path(data_dir) / 'ca'
        self.hosts = self.dir / 'hosts'
        self.ca_cert = self.dir / 'dopamine-ca.pem'
        self.ca_key = self.dir / 'dopamine-ca.key'
        self.leaf_key = self.dir / 'leaf.key'
        self.lock = threading.RLock()
        self.contexts = collections.OrderedDict()
        self._info = None

    def _run(self, *args, cwd=None):
        try:
            result = subprocess.run([OPENSSL, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except FileNotFoundError:
            raise ValueError(OPENSSL + ' is required for HTTPS interception')
        if result.returncode != 0:
            lines = result.stderr.decode('utf-8', 'replace').strip().splitlines()
            raise ValueError('openssl failed: ' + (lines[-1] if lines else 'exit status ' + str(result.returncode)))
        return result.stdout.decode('utf-8', 'replace')

    def exists(self):
        return self.ca_cert.is_file() and self.ca_key.is_file() and self.leaf_key.is_file()

    def ensure(self):
        with self.lock:
            if not self.exists():
                self.generate()

    def generate(self):
        """Create a new CA and shared leaf key, replacing any previous CA and issued certificates."""
        with self.lock:
            staging = Path(tempfile.mkdtemp(prefix='.ca-', dir=str(self.dir.parent)))
            try:
                os.chmod(str(staging), 0o700)
                (staging / 'openssl.cnf').write_text(CONFIG.format(san='DNS:localhost'))
                label = 'Dopamine CA ' + secrets.token_hex(4).upper()
                self._run('genrsa', '-out', 'dopamine-ca.key', '2048', cwd=str(staging))
                self._run('req', '-x509', '-new', '-key', 'dopamine-ca.key', '-sha256', '-days', str(CA_DAYS),
                          '-subj', '/CN=' + subject_value(label) + '/O=Dopamine/OU=Local interception CA',
                          '-config', 'openssl.cnf', '-extensions', 'v3_ca', '-out', 'dopamine-ca.pem', cwd=str(staging))
                self._run('genrsa', '-out', 'leaf.key', '2048', cwd=str(staging))
                (staging / 'openssl.cnf').unlink()
                (staging / 'hosts').mkdir(mode=0o700)
                for key in ('dopamine-ca.key', 'leaf.key'):
                    os.chmod(str(staging / key), 0o600)
                if self.dir.exists():
                    shutil.rmtree(str(self.dir))
                staging.rename(self.dir)
            finally:
                if staging.exists():
                    shutil.rmtree(str(staging), ignore_errors=True)
            self.contexts.clear()
            self._info = None

    def info(self):
        with self.lock:
            if not self.exists():
                return dict(exists=False, path=str(self.ca_cert))
            if self._info is None:
                pem = self.ca_cert.read_text()
                der = ssl.PEM_cert_to_DER_cert(pem)
                digest = hashlib.sha256(der).hexdigest().upper()
                out = self._run('x509', '-in', str(self.ca_cert), '-noout', '-subject', '-enddate')
                subject = re.search(r'CN\s*=\s*([^,/\n]+)', out)
                end = re.search(r'notAfter=(.+)', out)
                expires = ''
                if end:
                    try:
                        expires = datetime.datetime.strptime(end.group(1).strip(), '%b %d %H:%M:%S %Y %Z').date().isoformat()
                    except ValueError:
                        expires = end.group(1).strip()
                self._info = dict(exists=True, path=str(self.ca_cert), name=subject.group(1).strip() if subject else 'Dopamine CA',
                                  sha256=':'.join(digest[i:i + 2] for i in range(0, len(digest), 2)), expires=expires)
            return dict(self._info)

    def pem(self):
        self.ensure()
        return self.ca_cert.read_bytes()

    def der(self):
        return ssl.PEM_cert_to_DER_cert(self.pem().decode())

    def trusted(self):
        """Whether macOS trusts the CA for this user. Returns None when it cannot be determined."""
        if not self.exists() or not Path(SECURITY).exists():
            return None
        try:
            result = subprocess.run([SECURITY, 'verify-cert', '-c', str(self.ca_cert), '-p', 'basic', '-L', '-l', '-q'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.returncode == 0

    def context_for(self, host):
        """An SSL server context presenting a leaf certificate for host, issued on first use and cached."""
        host = normalize_host(host)
        with self.lock:
            self.ensure()
            context = self.contexts.get(host)
            if context is not None:
                self.contexts.move_to_end(host)
                return context
            cert = self.hosts / (host.replace(':', '_') + '.pem')
            if not cert.is_file():
                self._issue(host, cert)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            # The engine relays HTTP/1.1 only; advertising it makes HTTP/2 clients fall back.
            context.set_alpn_protocols(['http/1.1'])
            context.load_cert_chain(str(cert), str(self.leaf_key))
            self.contexts[host] = context
            while len(self.contexts) > CONTEXT_CACHE:
                self.contexts.popitem(last=False)
            return context

    def _issue(self, host, target):
        try:
            ipaddress.ip_address(host)
            san = 'IP:' + host
        except ValueError:
            san = 'DNS:' + host
        common = host if len(host) <= 64 else host[:64]
        with tempfile.TemporaryDirectory(dir=str(self.dir)) as work:
            Path(work, 'openssl.cnf').write_text(CONFIG.format(san=san))
            self._run('req', '-new', '-key', str(self.leaf_key), '-subj', '/CN=' + subject_value(common) + '/O=Dopamine',
                      '-config', 'openssl.cnf', '-out', 'leaf.csr', cwd=work)
            self._run('x509', '-req', '-in', 'leaf.csr', '-CA', str(self.ca_cert), '-CAkey', str(self.ca_key),
                      '-set_serial', '0x' + secrets.token_hex(15), '-days', str(LEAF_DAYS), '-sha256',
                      '-extfile', 'openssl.cnf', '-extensions', 'v3_leaf', '-out', 'leaf.pem', cwd=work)
            Path(work, 'leaf.pem').replace(target)
