"""Tests for the archive's keyed IP hash (GH #233) and where its key lives (GH #352)."""

import hashlib
import hmac
import io
import os
import threading
from pathlib import Path

import audit_ip_hmac as aih
import pytest

KEY_HEX = "ab" * 32


def _expected(ip: str, key_hex: str = KEY_HEX) -> str:
    return hmac.new(bytes.fromhex(key_hex), ip.encode(), hashlib.sha256).hexdigest()


def _creds(tmp_path: Path, content: str | bytes, key_id: str = "2026-10") -> dict[str, str]:
    """An env as the unit builds it: ``$CREDENTIALS_DIRECTORY`` holding the key, plus the ID."""
    cred_dir = tmp_path / "creds"
    cred_dir.mkdir(exist_ok=True)
    path = cred_dir / aih.CREDENTIAL
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return {aih.CREDENTIALS_DIR_ENV: str(cred_dir), aih.KEY_ID_ENV: key_id}


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return _creds(tmp_path, KEY_HEX + "\n")


def test_hash_is_hmac_sha256_hex_of_the_ip(env: dict) -> None:
    hasher = aih.load_hasher(env)
    assert hasher.key_id == "2026-10"
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7")
    assert len(hasher.hash("203.0.113.7")) == 64


def test_hash_is_never_a_plain_sha256(env: dict) -> None:
    """An unkeyed SHA-256 of an IPv4 address is brute-forceable (#228, #233)."""
    hasher = aih.load_hasher(env)
    assert hasher.hash("203.0.113.7") != hashlib.sha256(b"203.0.113.7").hexdigest()


def test_different_keys_give_different_hashes(env: dict, tmp_path: Path) -> None:
    (tmp_path / "other").write_text("cd" * 32)
    other = aih.load_hasher(env, str(tmp_path / "other"))
    assert other.hash("203.0.113.7") != aih.load_hasher(env).hash("203.0.113.7")


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


def test_ipv6_spellings_hash_alike(env: dict) -> None:
    hasher = aih.load_hasher(env)
    assert hasher.hash("2001:DB8:0:0:0:0:0:1") == hasher.hash("2001:db8::1")
    assert hasher.hash("2001:db8::1") == _expected("2001:db8::1")


def test_repr_never_shows_the_key(env: dict) -> None:
    hasher = aih.load_hasher(env)
    assert KEY_HEX not in repr(hasher)
    assert KEY_HEX not in str(hasher)
    assert "2026-10" in repr(hasher)


# --- where the key comes from (#352) ---------------------------------------


def test_key_file_overrides_the_credential(env: dict, tmp_path: Path) -> None:
    """The hand-run tools' ``--key-file`` wins; the unit passes none."""
    other = "cd" * 32
    (tmp_path / "k").write_text(other)
    hasher = aih.load_hasher(env, str(tmp_path / "k"))
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7", other)


def test_key_file_alone_needs_no_credentials_directory(tmp_path: Path) -> None:
    (tmp_path / "k").write_text(KEY_HEX)
    hasher = aih.load_hasher({aih.KEY_ID_ENV: "2026-10"}, str(tmp_path / "k"))
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7")


def test_key_is_read_through_a_pipe(tmp_path: Path) -> None:
    """``--key-file <(sudo cat ...)`` hands over a FIFO path, read once."""
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    writer = threading.Thread(target=lambda: fifo.write_text(KEY_HEX + "\n"))
    writer.start()
    hasher = aih.load_hasher({aih.KEY_ID_ENV: "2026-10"}, str(fifo))
    writer.join()
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7")


def test_the_env_var_is_refused_even_beside_a_valid_credential(env: dict) -> None:
    """A key in an environment is the exposure #352 removed: refuse it, never use it."""
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher({**env, aih.KEY_ENV: "cd" * 32})
    assert aih.KEY_ENV in str(exc.value)
    assert "cd" * 32 not in str(exc.value)


def test_env_var_alone_is_not_a_key(tmp_path: Path) -> None:
    """No fallback to the retired variable."""
    with pytest.raises(aih.KeyConfigError):
        aih.load_hasher({aih.KEY_ENV: KEY_HEX, aih.KEY_ID_ENV: "k1"})


def test_no_source_names_both_ways_to_supply_the_key() -> None:
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher({aih.KEY_ID_ENV: "k1"})
    assert aih.CREDENTIAL in str(exc.value)
    assert "--key-file" in str(exc.value)


@pytest.mark.parametrize(
    "content",
    [
        "",  # an empty key file
        "  \n",
        "zz" * 32,
        "abc",
        "ab" * 31,
        b"\xff" * 64,
        ("ab" * 32)[:-1] + "\xe9",
    ],
)
def test_bad_key_is_refused_without_echoing_it(tmp_path: Path, content: str | bytes) -> None:
    """Every refusal names the source and never contains the key."""
    env = _creds(tmp_path, content)
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher(env)
    message = str(exc.value)
    assert aih.CREDENTIAL in message
    text = content.decode("latin-1") if isinstance(content, bytes) else content
    if text.strip():
        assert text.strip() not in message
    assert "\\xff" not in message
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_unit_fallback_names_the_missing_key_file(tmp_path: Path) -> None:
    """A missing file reaches the script as SetCredential='s lone newline: say where to look."""
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher(_creds(tmp_path, "\n"))
    assert "missing or empty" in str(exc.value)
    assert aih.KEY_FILE in str(exc.value)


def test_oversized_key_file_is_refused_unread() -> None:
    """``--key-file /dev/urandom`` by mistake stops at the cap, not at OOM."""
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher({aih.KEY_ID_ENV: "k1"}, "/dev/zero")
    assert "not a key file" in str(exc.value)


def test_missing_credential_file_is_refused(tmp_path: Path) -> None:
    env = {aih.CREDENTIALS_DIR_ENV: str(tmp_path), aih.KEY_ID_ENV: "k1"}
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher(env)
    assert aih.CREDENTIAL in str(exc.value)
    assert exc.value.__cause__ is None


def test_unreadable_key_file_names_the_flag(tmp_path: Path) -> None:
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher({aih.KEY_ID_ENV: "k1"}, str(tmp_path / "nope"))
    assert "--key-file" in str(exc.value)


@pytest.mark.parametrize("key_id", [None, "", "has space", "x" * 33])
def test_bad_key_id_is_refused(tmp_path: Path, key_id: str | None) -> None:
    env = _creds(tmp_path, KEY_HEX)
    if key_id is None:
        del env[aih.KEY_ID_ENV]
    else:
        env[aih.KEY_ID_ENV] = key_id
    with pytest.raises(aih.KeyConfigError) as exc:
        aih.load_hasher(env)
    assert aih.KEY_ID_ENV in str(exc.value)
    assert KEY_HEX not in str(exc.value)


def test_key_whitespace_is_stripped(tmp_path: Path) -> None:
    """A trailing newline or space in the key file is not part of the key."""
    hasher = aih.load_hasher(_creds(tmp_path, f" {KEY_HEX}\r\n", key_id=" 2026-10 "))
    assert hasher.key_id == "2026-10"
    assert hasher.hash("203.0.113.7") == _expected("203.0.113.7")


# --- CLI ---------------------------------------------------------------------


def test_cli_hashes_ip_from_stdin(env: dict, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("203.0.113.7\n"))
    assert aih.cli([], env) == 0
    out = capsys.readouterr()
    assert out.out == f"2026-10 {_expected('203.0.113.7')}\n"
    assert "203.0.113.7" not in out.out + out.err
    assert KEY_HEX not in out.out + out.err


def test_cli_prompts_off_screen_on_a_tty(env: dict, monkeypatch, capsys) -> None:
    """On a terminal the IP is read with getpass, never echoed or taken from argv."""
    tty = io.StringIO()
    tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", tty)
    monkeypatch.setattr(aih.getpass, "getpass", lambda prompt: "2001:DB8::1")
    assert aih.cli([], env) == 0
    assert capsys.readouterr().out == f"2026-10 {_expected('2001:db8::1')}\n"


def test_cli_rejects_empty_input(env: dict, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert aih.cli([], env) == 1
    assert "no IP" in capsys.readouterr().err


def test_cli_check_prints_only_id_and_length(env: dict, capsys) -> None:
    assert aih.cli(["--check"], env) == 0
    out = capsys.readouterr()
    assert out.out == "key id 2026-10, 32 bytes: ok\n"
    assert KEY_HEX not in out.out + out.err


def test_cli_check_reads_key_file(tmp_path: Path, capsys) -> None:
    (tmp_path / "k").write_text(KEY_HEX)
    assert aih.cli(["--check", "--key-file", str(tmp_path / "k")], {aih.KEY_ID_ENV: "k9"}) == 0
    assert capsys.readouterr().out == "key id k9, 32 bytes: ok\n"


def test_cli_refuses_without_key(capsys) -> None:
    assert aih.cli(["--check"], {aih.KEY_ID_ENV: "k1"}) == 1
    assert "--key-file" in capsys.readouterr().err


def test_cli_refuses_the_env_var(env: dict, capsys) -> None:
    assert aih.cli(["--check"], {**env, aih.KEY_ENV: KEY_HEX}) == 1
    err = capsys.readouterr().err
    assert aih.KEY_ENV in err
    assert KEY_HEX not in err
