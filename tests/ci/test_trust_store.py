"""The CA bundle every outbound httpx2 call trusts: certifi's, alone (#343, #345, #420).

httpx read certifi; httpx2 defaults to the OS store (truststore). On this host
the OS store still trusts emSign C1 and ECC C3, which certifi 2026.10.23 dropped
(CannObserv/provisioner#77), so moving would widen trust silently. certifi is
kept because the code says so: every httpx2 client or request under ``src/``,
``infra/`` and ``scripts/`` passes ``verify=trusted()``. Modeled on co-status's
``tests/ci/test_trust_store.py`` (CannObserv/status#125).

``trusted()`` exists twice: ``address_validator.core.tls`` for the service, and
``infra/tls.py`` for the ops scripts, because the patching recovery knob runs
``infra/patching_recovery_check.py`` with no PYTHONPATH and cannot import the
service package. Both are checked here.

So the checks are three: the bundle lacks the distrusted root, each
``trusted()`` holds that bundle and nothing else, and no call site goes
without one.

``emSign ECC Root CA - G3`` is the root the leaked eMudhra intermediates chain
to (PYSEC-2026-4320, #343). certifi 2026.10.19 dropped it; a lock resolving an
older certifi fails here rather than shipping. The roots are read as
certificates, the way the clients load them, not matched in the bundle's
``# Subject:`` comments.
"""

import ast
import ssl
from pathlib import Path

import certifi
import pytest
import tls as infra_tls

from address_validator.core import tls as service_tls

REPO_ROOT = Path(__file__).resolve().parents[2]

AREAS = ("src", "infra", "scripts")
DISTRUSTED_ROOTS = {"emSign ECC Root CA - G3"}

#: The modules a call site may take ``trusted`` from.
TRUSTED_MODULES = {"address_validator.core.tls", "tls"}

TRUSTED = pytest.mark.parametrize(
    "trusted", [service_tls.trusted, infra_tls.trusted], ids=["service", "infra"]
)


def _certifi_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def test_bundle_does_not_trust_distrusted_roots():
    """No distrusted root is among the bundle's roots, and roots were read at all."""
    names = {
        value
        for cert in _certifi_context().get_ca_certs()
        for rdn in cert["subject"]
        for key, value in rdn
        if key == "commonName"
    }
    assert names, f"no roots read from {certifi.where()}"
    assert names & DISTRUSTED_ROOTS == set(), certifi.__version__


@TRUSTED
def test_our_clients_trust_certifi_and_nothing_else(trusted):
    """``trusted()`` holds exactly certifi's roots: no OS store, no default paths."""
    ours = trusted().get_ca_certs(binary_form=True)
    assert ours, "trusted() loaded no roots"
    assert sorted(ours) == sorted(_certifi_context().get_ca_certs(binary_form=True))


@TRUSTED
def test_our_clients_verify_hostnames(trusted):
    context = trusted()
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname


#: httpx2's names that open a connection, each taking ``verify=``.
HTTPX2_CALLS = {
    "Client",
    "AsyncClient",
    "request",
    "stream",
    "get",
    "options",
    "head",
    "post",
    "put",
    "patch",
    "delete",
}


def _parsed() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for area in AREAS
        for path in sorted((REPO_ROOT / area).rglob("*.py"))
    ]


def _call_sites() -> list[tuple[str, ast.Call, ast.Module]]:
    """Every ``httpx2.<name>(...)`` call that opens a connection."""
    sites = []
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx2"
                and node.func.attr in HTTPX2_CALLS
            ):
                sites.append((f"{path.relative_to(REPO_ROOT)}:{node.lineno}", node, tree))
    return sites


def _imports_trusted(tree: ast.Module) -> bool:
    """The file takes ``trusted`` from one of the two tls modules, not elsewhere."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module in TRUSTED_MODULES
        and any(alias.name == "trusted" and alias.asname is None for alias in node.names)
        for node in ast.walk(tree)
    )


def test_every_client_was_found():
    """registry, libpostal, status_checkin, patching_recovery_check: none found passes vacuously."""
    assert len(_call_sites()) >= 4


@pytest.mark.parametrize("site", _call_sites(), ids=lambda site: site[0])
def test_every_client_verifies_against_certifi(site):
    where, call, tree = site
    verify = [kw.value for kw in call.keywords if kw.arg == "verify"]
    assert (
        verify
        and isinstance(verify[0], ast.Call)
        and isinstance(verify[0].func, ast.Name)
        and verify[0].func.id == "trusted"
        and not verify[0].args
        and _imports_trusted(tree)
    ), (
        f"{where} opens an httpx2 connection without verify=trusted() from a tls "
        f"module, so it trusts the OS store rather than certifi's bundle (#420)."
    )


@pytest.mark.parametrize("area", AREAS)
def test_httpx2_is_reached_through_its_module(area):
    """``from httpx2 import AsyncClient`` would hide a call site from the walk above."""
    for path in sorted((REPO_ROOT / area).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "httpx2":
                names = {alias.name for alias in node.names} & HTTPX2_CALLS
                assert not names, f"{path.relative_to(REPO_ROOT)} imports {names} from httpx2"
