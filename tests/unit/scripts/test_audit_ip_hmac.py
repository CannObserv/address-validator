"""Tests for the archive's keyed IP hash (GH #233)."""

import hashlib
import hmac
import io

import audit_ip_hmac as aih
import pytest

KEY_HEX = "ab" * 32
ENV = {aih.KEY_ENV: KEY_HEX, aih.KEY_ID_ENV: "2026-10"}


def _expected(ip: str, key_hex: str = KEY_HEX) -> str:
    return hmac.new(bytes.fromhex(key_hex), ip.encode(), hashlib.sha256).hexdigest()


def test_hash_is_hmac_sha256_hex_of_the_ip() -> None:
    hasher = aih.load_hasher(ENV)
    assert hasher.key_id == "2026-10"
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7")
    assert len(hasher.hash("203.0.113.7")) == 64


def test_hash_is_never_a_plain_sha256() -> None:
    """An unkeyed SHA-256 of an IPv4 address is brute-forceable (#228, #233)."""
    hasher = aih.load_hasher(ENV)
    assert hasher.hash("203.0.113.7") != hashlib.sha256(b"203.0.113.7").hexdigest()


def test_different_keys_give_different_hashes() -> None:
    other = aih.load_hasher({aih.KEY_ENV: "cd" * 32, aih.KEY_ID_ENV: "k2"})
    assert other.hash("203.0.113.7") != aih.load_hasher(ENV).hash("203.0.113.7")


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        ("unknown", "unknown"),
        ("not an ip", "not an ip"),
    ],
)
def test_canonical_ip(raw: str, canonical: str) -> None:
    assert aih.canonical_ip(raw) == canonical


def test_ipv6_spellings_hash_alike() -> None:
    hasher = aih.load_hasher(ENV)
    assert hasher.hash("2001:DB8:0:0:0:0:0:1") == hasher.hash("2001:db8::1")
    assert hasher.hash("2001:db8::1") == _expected("2001:db8::1")


def test_repr_never_shows_the_key() -> None:
    hasher = aih.load_hasher(ENV)
    assert KEY_HEX not in repr(hasher)
    assert KEY_HEX not in str(hasher)
    assert "2026-10" in repr(hasher)


@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({}, aih.KEY_ENV),
        ({aih.KEY_ID_ENV: "k1"}, aih.KEY_ENV),
        ({aih.KEY_ENV: "  ", aih.KEY_ID_ENV: "k1"}, aih.KEY_ENV),
        ({aih.KEY_ENV: "zz" * 32, aih.KEY_ID_ENV: "k1"}, aih.KEY_ENV),
        ({aih.KEY_ENV: "abc", aih.KEY_ID_ENV: "k1"}, aih.KEY_ENV),
        ({aih.KEY_ENV: "ab" * 31, aih.KEY_ID_ENV: "k1"}, aih.KEY_ENV),
        ({aih.KEY_ENV: KEY_HEX}, aih.KEY_ID_ENV),
        ({aih.KEY_ENV: KEY_HEX, aih.KEY_ID_ENV: ""}, aih.KEY_ID_ENV),
        ({aih.KEY_ENV: KEY_HEX, aih.KEY_ID_ENV: "has space"}, aih.KEY_ID_ENV),
        ({aih.KEY_ENV: KEY_HEX, aih.KEY_ID_ENV: "x" * 33}, aih.KEY_ID_ENV),
    ],
)
def test_load_hasher_rejects_bad_config_without_echoing_it(env: dict, names: str) -> None:
    """Every refusal names the variable and never contains a value."""
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher(env)
    message = str(exc.value)
    assert names in message
    for value in env.values():
        if value.strip():
            assert value not in message
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_key_whitespace_is_stripped() -> None:
    """A trailing newline or space in the env file is not part of the key."""
    hasher = aih.load_hasher({aih.KEY_ENV: f" {KEY_HEX}\n", aih.KEY_ID_ENV: " 2026-10 "})
    assert hasher.key_id == "2026-10"
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7")


def test_cli_hashes_ip_from_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("203.0.113.7\n"))
    assert aih.cli([], ENV) == 0
    out = capsys.readouterr()
    assert out.out == f"2026-10 {_expected('203.0.113.7')}\n"
    assert "203.0.113.7" not in out.out + out.err
    assert KEY_HEX not in out.out + out.err


def test_cli_prompts_off_screen_on_a_tty(monkeypatch, capsys) -> None:
    """On a terminal the IP is read with getpass, never echoed or taken from argv."""
    tty = io.StringIO()
    tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", tty)
    monkeypatch.setattr(aih.getpass, "getpass", lambda prompt: "2001:DB8::1")
    assert aih.cli([], ENV) == 0
    assert capsys.readouterr().out == f"2026-10 {_expected('2001:db8::1')}\n"


def test_cli_rejects_empty_input(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert aih.cli([], ENV) == 1
    assert "no IP" in capsys.readouterr().err


def test_cli_check_prints_only_id_and_length(capsys) -> None:
    assert aih.cli(["--check"], ENV) == 0
    out = capsys.readouterr()
    assert out.out == "key id 2026-10, 32 bytes: ok\n"
    assert KEY_HEX not in out.out + out.err


def test_cli_refuses_without_key(capsys) -> None:
    assert aih.cli(["--check"], {}) == 1
    assert aih.KEY_ENV in capsys.readouterr().err
