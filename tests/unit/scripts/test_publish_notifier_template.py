"""Tests for the notifier template publisher (GH #232). No request reaches notifier."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar

import notify_unit_failure as nuf
import publish_notifier_template as ptpl
import pytest

TEMPLATE = json.loads(ptpl.TEMPLATE.read_text())
ENV = {"NOTIFIER_URL": "http://notifier.test:9000", "NOTIFIER_API_KEY": "nk_test"}


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self, *, environment="production", preview_error=None, **kwargs):
        self.kwargs = kwargs
        self.environment = environment
        self.preview_error = preview_error
        self.calls: list[tuple] = []
        self.templates = SimpleNamespace(create=self._create, update=self._update)
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def health(self):
        return {"environment": self.environment}

    async def preview(self, **kwargs):
        self.calls.append(("preview", kwargs))
        return SimpleNamespace(error=self.preview_error, title="t", body="b")

    async def _create(self, **fields):
        self.calls.append(("create", fields))
        return SimpleNamespace(id="01NEWTEMPLATE")

    async def _update(self, template_id, **fields):
        self.calls.append(("update", template_id, fields))
        return SimpleNamespace(id=template_id)


@pytest.fixture
def fake(monkeypatch):
    FakeClient.instances = []

    def install(**kwargs):
        monkeypatch.setattr(ptpl, "NotifierClient", lambda **kw: FakeClient(**kwargs, **kw))

    install()
    return install


def _written(client):
    return [c for c in client.calls if c[0] != "preview"]


# --- env file ----------------------------------------------------------------


def test_read_notifier_env_keeps_only_notifier_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VALIDATION_CACHE_DSN=postgresql://prod\n"
        "# NOTIFIER_URL=commented\n"
        "NOTIFIER_URL=http://notifier:9000\n"
        'NOTIFIER_API_KEY="nk_quoted"\n'
        "  NOTIFIER_UNIT_FAILURE_TEMPLATE_ID = '01TPL'  \n"
    )
    assert ptpl.read_notifier_env(env_file) == {
        "NOTIFIER_URL": "http://notifier:9000",
        "NOTIFIER_API_KEY": "nk_quoted",
        "NOTIFIER_UNIT_FAILURE_TEMPLATE_ID": "01TPL",
    }


# --- publish -----------------------------------------------------------------


async def test_publish_creates_without_template_id(fake):
    assert await ptpl.publish(ENV, TEMPLATE, dry_run=False) == "01NEWTEMPLATE"
    (client,) = FakeClient.instances
    ((action, fields),) = _written(client)
    assert action == "create"
    assert fields["name"] == TEMPLATE["name"]
    assert fields["variables_schema"] == TEMPLATE["variables_schema"]


async def test_publish_updates_existing_template(fake):
    env = {**ENV, "NOTIFIER_UNIT_FAILURE_TEMPLATE_ID": "01EXISTING"}
    assert await ptpl.publish(env, TEMPLATE, dry_run=False) == "01EXISTING"
    ((action, template_id, _),) = _written(FakeClient.instances[0])
    assert (action, template_id) == ("update", "01EXISTING")


async def test_publish_dry_run_writes_nothing(fake):
    await ptpl.publish(ENV, TEMPLATE, dry_run=True)
    client = FakeClient.instances[0]
    assert _written(client) == []
    assert client.calls[0][1]["variables"] == TEMPLATE["sample_variables"]


async def test_publish_refuses_non_production(fake):
    fake(environment="development")
    with pytest.raises(SystemExit, match="environment=development"):
        await ptpl.publish(ENV, TEMPLATE, dry_run=False)
    assert _written(FakeClient.instances[0]) == []


async def test_publish_refuses_unrenderable_template(fake):
    fake(preview_error="'unit' is undefined")
    with pytest.raises(SystemExit, match="does not render"):
        await ptpl.publish(ENV, TEMPLATE, dry_run=False)
    assert _written(FakeClient.instances[0]) == []


def test_main_requires_url_and_key(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("NOTIFIER_API_KEY=nk_x\n")
    monkeypatch.setattr("sys.argv", ["publish", "--env-file", str(env_file)])
    assert ptpl.main() == 1
    assert "missing NOTIFIER_URL" in capsys.readouterr().err


# --- template ↔ handler drift ------------------------------------------------


async def test_handler_sends_every_variable_the_template_requires():
    """A required variable the handler never sends is a 422 — and a lost alert."""
    sent = {}

    class Capture:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def health(self):
            return {"environment": "production"}

        async def dispatch(self, **kwargs):
            sent.update(kwargs["variables"])
            return SimpleNamespace(id="x", status="succeeded", attempts=[])

    config = nuf.NotifierConfig("http://n", "nk", "01T", ["01C"])
    failure = nuf.failure_from_env("x.service", {})
    await nuf.notify(config, failure, "tail", host="h", client_factory=Capture)
    schema = TEMPLATE["variables_schema"]
    assert set(schema["required"]) <= set(sent)
    assert set(schema["required"]) <= set(TEMPLATE["sample_variables"])
