"""Pure unit tests: naming, SSRF guard, security primitives, migrations vs models."""

import ipaddress

import pytest
from alembic import command

from conftest import alembic_config
from gateway import naming
from gateway.security import CredentialBox, KEY_PREFIX, hash_api_key, hash_password, new_api_key, verify_password
from gateway.ssrf import UnsafeUrlError, check_upstream_url

pytestmark = pytest.mark.anyio


# --- naming -----------------------------------------------------------------

def test_prefix_round_trip():
    assert naming.prefixed("notes", "add_note") == "notes__add_note"
    assert naming.split("notes__add_note") == ("notes", "add_note")


def test_split_uses_first_separator_so_tool_names_may_contain_it():
    assert naming.split("woo__weird__tool") == ("woo", "weird__tool")


@pytest.mark.parametrize("name", ["add_note", "__x", "notes__", "Notes__x", "no_tes__x", "-a__x"])
def test_invalid_prefixed_names(name):
    assert naming.split(name) is None


@pytest.mark.parametrize("slug,ok", [("woo", True), ("my-crm2", True), ("a", True),
                                      ("Woo", False), ("my_crm", False), ("-x", False), ("x-", False), ("", False)])
def test_slug_rules(slug, ok):
    assert naming.valid_slug(slug) is ok


# --- SSRF guard ---------------------------------------------------------------

DNS = {
    "mcp.example.com": ["93.184.216.34"],
    "metadata.evil.example": ["169.254.169.254"],
    "loopback.evil.example": ["127.0.0.1"],
    "internal.evil.example": ["10.43.0.12"],
    "mapped.evil.example": ["::ffff:127.0.0.1"],
    "half.evil.example": ["93.184.216.34", "10.0.0.5"],   # every address must be safe
    "notes-mcp.platform.svc.cluster.local": ["10.43.114.37"],
}
ALLOW = {"notes-mcp.platform.svc.cluster.local"}


async def fake_resolve(host, port):
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return [ipaddress.ip_address(a) for a in DNS[host]]


@pytest.mark.parametrize("url", [
    "https://mcp.example.com/mcp",
    "http://notes-mcp.platform.svc.cluster.local:8000/mcp",     # allowlisted cluster name
])
async def test_ssrf_allows(url):
    await check_upstream_url(url, ALLOW, resolve=fake_resolve)


@pytest.mark.parametrize("url,why", [
    ("http://169.254.169.254/latest/meta-data/", "forbidden"),       # cloud metadata, literal
    ("http://metadata.evil.example/", "forbidden"),                   # ...via DNS
    ("http://localhost:8000/mcp", "not in the allowlist"),            # no dot = cluster-internal name
    ("http://127.0.0.1:8000/mcp", "forbidden"),
    ("http://loopback.evil.example/", "forbidden"),
    ("http://mapped.evil.example/", "forbidden"),                     # IPv4-mapped IPv6 loopback
    ("http://10.0.0.5/mcp", "internal address"),                      # pod/service IPs by literal
    ("http://internal.evil.example/", "internal address"),
    ("http://half.evil.example/", "internal address"),
    ("http://fx-mcp.platform.svc.cluster.local:8000/mcp", "not in the allowlist"),
    ("http://postgres:5432/", "not in the allowlist"),
    ("ftp://mcp.example.com/", "only http"),
    ("http://user:pw@mcp.example.com/", "credentials in the URL"),
    ("http://nonexistent.example/", "cannot resolve"),
    ("http:///nohost", "no host"),
])
async def test_ssrf_blocks(url, why):
    with pytest.raises(UnsafeUrlError, match=why):
        await check_upstream_url(url, ALLOW, resolve=fake_resolve)


# --- security primitives ------------------------------------------------------

def test_api_key_format_prefix_and_hash():
    key, prefix, key_hash = new_api_key()
    assert key.startswith(KEY_PREFIX) and len(key) >= len(KEY_PREFIX) + 43   # 32 bytes base64url
    assert key.startswith(prefix) and len(prefix) == 12
    assert key_hash == hash_api_key(key) and len(key_hash) == 64 and key not in key_hash
    assert new_api_key()[0] != key


def test_password_hashing():
    h = hash_password("correct horse")
    assert h.startswith("$argon2id$") and "correct horse" not in h
    assert verify_password(h, "correct horse")
    assert not verify_password(h, "wrong")
    assert not verify_password("garbage", "x")


def test_credential_box_round_trip_and_wrong_key():
    from cryptography.fernet import Fernet
    box = CredentialBox(Fernet.generate_key().decode())
    token = box.encrypt("s3cret")
    assert "s3cret" not in token and box.decrypt(token) == "s3cret"
    with pytest.raises(ValueError, match="wrong GATEWAY_FERNET_KEY"):
        CredentialBox(Fernet.generate_key().decode()).decrypt(token)


# --- migrations ---------------------------------------------------------------

def test_migrations_match_models(database_url):
    # Fails if a model changed without a matching Alembic migration.
    command.check(alembic_config(database_url))
