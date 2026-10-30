"""HTTPS on the shop network: a local certificate authority of our own.

    python serve.py --https

No public CA will sign a certificate for 192.168.1.20, so the server makes
its own small CA once (`~/.autorundown/tls/ca.crt`, 10 years) and signs a
server certificate for every name and address this PC answers on. A phone
that installs `ca.crt` (served at /ca.crt) then gets a padlock with no
warning; one that does not still gets an encrypted connection, after
clicking through a warning.

The CA is limited by Name Constraints to private addresses and to
`localhost` / `.local` names, so even if its key leaked, it could not be
used to impersonate a real website to a phone that trusts it.

The server certificate is re-issued at start whenever this PC's addresses
are not all in it (a new DHCP lease) or it has under 30 days left. Its
lifetime is 397 days, inside what Apple and Chrome accept.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

from . import config

TLS_DIR = config.APP_DIR / "tls"
CA_CRT, CA_KEY = TLS_DIR / "ca.crt", TLS_DIR / "ca.key"
SRV_CRT, SRV_KEY = TLS_DIR / "server.crt", TLS_DIR / "server.key"

PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"]


def _imports():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError as exc:
        raise SystemExit("HTTPS needs the 'cryptography' package: "
                         "pip install -r requirements.txt") from exc
    return x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID


def local_names() -> tuple[list[str], list[str]]:
    """(DNS names, IP addresses) this PC can be reached by."""
    host = socket.gethostname().split(".")[0].lower()
    names = ["localhost"] + ([f"{host}.local"] if host else [])
    ips = {"127.0.0.1", "::1"}
    for addr in _lan_addresses():
        ips.add(addr)
    return names, sorted(ips)


def _lan_addresses() -> list[str]:
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            out.add(info[4][0].split("%")[0])
    except (OSError, UnicodeError):  # UnicodeError: a host name too long to look up
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet is sent; picks the LAN route
        out.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    return [a for a in out if _private(a)]


def _private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(n) for n in PRIVATE_NETS)


def _write_key(path: Path, key, serialization) -> None:
    """Created private (0600) from the start, not written and then chmod'ed:
    for that moment the key was readable by others."""
    data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ensure_ca():
    x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID = _imports()
    if CA_CRT.exists() and CA_KEY.exists():
        return (x509.load_pem_x509_certificate(CA_CRT.read_bytes()),
                serialization.load_pem_private_key(CA_KEY.read_bytes(), None))
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    # a common name is at most 64 characters; a Mac's host name can be longer
    host = socket.gethostname().split(".")[0][:40] or "this PC"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"AutoRundown local CA ({host})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AutoRundown")])
    now = _now()
    permitted = [x509.DNSName("localhost"), x509.DNSName("local")]
    permitted += [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(permitted_subtrees=permitted,
                                                excluded_subtrees=None), critical=True)
            # web servers only: a leaked key can't sign code or e-mail that a
            # phone with this CA installed would trust
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(CA_KEY, key, serialization)
    CA_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def _server_ok(names: list[str], ips: list[str]) -> bool:
    x509, *_ = _imports()
    if not (SRV_CRT.exists() and SRV_KEY.exists()):
        return False
    cert = x509.load_pem_x509_certificate(SRV_CRT.read_bytes())
    if cert.not_valid_after_utc - _now() < dt.timedelta(days=30):
        return False
    try:
        ca = x509.load_pem_x509_certificate(CA_CRT.read_bytes())
        if cert.issuer != ca.subject:
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:
        return False
    have_dns = set(san.get_values_for_type(x509.DNSName))
    have_ip = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    return set(names) <= have_dns and set(ips) <= have_ip


def ensure_server_cert() -> tuple[Path, Path, list[str], list[str]]:
    """(cert file, key file, names, ips), issuing a new cert if needed."""
    x509, hashes, serialization, ec, EKU, NameOID = _imports()
    ca_cert, ca_key = ensure_ca()
    names, ips = local_names()
    if _server_ok(names, ips):
        return SRV_CRT, SRV_KEY, names, ips
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[-1][:64])]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([EKU.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(SRV_KEY, key, serialization)
    SRV_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return SRV_CRT, SRV_KEY, names, ips
