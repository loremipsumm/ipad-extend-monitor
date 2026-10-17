"""
Local CA + server certificate for the HTTPS endpoint.

Safari only exposes WebCodecs (the hardware H.264 decoder) on secure pages, so
the H.264 mode needs HTTPS. The CA is name-constrained to private LAN IPs and a
dummy .invalid domain, so even after the iPad trusts it, it cannot be used to
impersonate any real website.
"""
import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]
# Inside the CA's permitted DNS subtree: some verifiers (Apple's included) may check the
# subject CN against DNS name constraints, so it must not be an arbitrary string.
LEAF_NAME = "ipad-extend.invalid"
LEAF_DAYS = 397  # Apple rejects TLS leaf certificates valid for longer than ~398 days


def _key_usage(**on):
    flags = dict(
        digital_signature=False, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=False,
        crl_sign=False, encipher_only=False, decipher_only=False,
    )
    flags.update(on)
    return x509.KeyUsage(**flags)


def _save(path, key=None, cert=None):
    if key is not None:
        path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    else:
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _make_ca(now):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iPad Extend Monitor Local CA")])
    permitted = [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
    permitted.append(x509.DNSName(LEAF_NAME))
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(digital_signature=True, key_cert_sign=True, crl_sign=True), critical=True)
        .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_leaf(ca_key, ca_cert, ip, now):
    key = ec.generate_private_key(ec.SECP256R1())
    ips = sorted({ipaddress.ip_address(ip), ipaddress.ip_address("127.0.0.1")})
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, LEAF_NAME)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(digital_signature=True), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(LEAF_NAME)] + [x509.IPAddress(a) for a in ips]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _leaf_ok(path, ca_cert, ip, now):
    if not path.exists():
        return False
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    ips = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
        x509.IPAddress
    )
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return (
        cert.issuer == ca_cert.subject
        and cn and cn[0].value == LEAF_NAME
        and ipaddress.ip_address(ip) in ips
        and cert.not_valid_after_utc > now + datetime.timedelta(days=7)
    )


def ensure_certs(cert_dir: Path, ip: str):
    """Return (server_crt, server_key, ca_crt) paths, creating/renewing them as needed."""
    cert_dir.mkdir(exist_ok=True)
    ca_key_p, ca_crt_p = cert_dir / "ca.key", cert_dir / "ca.crt"
    key_p, crt_p = cert_dir / "server.key", cert_dir / "server.crt"
    now = datetime.datetime.now(datetime.timezone.utc)

    if ca_key_p.exists() and ca_crt_p.exists():
        ca_key = serialization.load_pem_private_key(ca_key_p.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_crt_p.read_bytes())
    else:
        ca_key, ca_cert = _make_ca(now)
        _save(ca_key_p, key=ca_key)
        _save(ca_crt_p, cert=ca_cert)

    if not (key_p.exists() and _leaf_ok(crt_p, ca_cert, ip, now)):
        key, cert = _make_leaf(ca_key, ca_cert, ip, now)
        _save(key_p, key=key)
        _save(crt_p, cert=cert)

    return crt_p, key_p, ca_crt_p
