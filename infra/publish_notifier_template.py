#!/usr/bin/env python3
"""Create or update a stored notifier template (#232, #249).

The wording lives here, in ``infra/notifier/<name>.json``; notifier stores a
copy under this tenant. Run once to create it, then again after editing the
JSON to push the change:

    .venv/bin/python infra/publish_notifier_template.py                        # unit-failure
    .venv/bin/python infra/publish_notifier_template.py --template archive-age
    .venv/bin/python infra/publish_notifier_template.py --dry-run              # render sample only

Reads only ``NOTIFIER_*`` keys from ``--env-file`` (default
``/etc/address-validator/.env``) so the production DSN never enters the process
environment. With the template's ID variable set (``TEMPLATES``) it PATCHes that
template; without it, it creates one and prints the ULID to put in that variable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from notifier_client import NotifierClient

NOTIFIER_DIR = Path(__file__).resolve().parent / "notifier"
# Template name → the env var holding its stored ULID, read by its sender.
TEMPLATES = {
    "unit-failure": "NOTIFIER_UNIT_FAILURE_TEMPLATE_ID",
    "archive-age": "NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID",
}
TEMPLATE = NOTIFIER_DIR / "unit-failure.json"
DEFAULT_ENV_FILE = Path("/etc/address-validator/.env")
EXPECTED_ENVIRONMENT = "production"


def read_notifier_env(path: Path) -> dict[str, str]:
    """``NOTIFIER_*`` assignments from an EnvironmentFile-style file."""
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("NOTIFIER_") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    return env


async def publish(
    env: dict[str, str],
    template: dict[str, Any],
    *,
    dry_run: bool,
    id_var: str = TEMPLATES["unit-failure"],
) -> str:
    async with NotifierClient(base_url=env["NOTIFIER_URL"], api_key=env["NOTIFIER_API_KEY"]) as c:
        environment = (await c.health()).get("environment")
        if environment != EXPECTED_ENVIRONMENT:
            raise SystemExit(f"notifier reports environment={environment}, expected production")
        preview = await c.preview(
            title_template=template["title_template"],
            body_template=template["body_template"],
            variables=template["sample_variables"],
            variables_schema=template["variables_schema"],
        )
        if preview.error:
            raise SystemExit(f"template does not render: {preview.error}")
        print(f"--- preview\n{preview.title}\n\n{preview.body}\n---")
        if dry_run:
            return "(dry run)"
        fields = {k: template[k] for k in ("name", "title_template", "body_template")}
        fields |= {k: template[k] for k in ("variables_schema", "sample_variables", "tags")}
        template_id = env.get(id_var, "").strip()
        if template_id:
            out = await c.templates.update(template_id, **fields)
            print(f"updated template {out.id}")
        else:
            out = await c.templates.create(**fields)
            print(f"created template {out.id}; set {id_var}={out.id}")
        return out.id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--template", choices=sorted(TEMPLATES), default="unit-failure")
    parser.add_argument("--dry-run", action="store_true", help="render the sample; write nothing")
    args = parser.parse_args()
    env = read_notifier_env(args.env_file)
    missing = [k for k in ("NOTIFIER_URL", "NOTIFIER_API_KEY") if not env.get(k)]
    if missing:
        print(f"{args.env_file}: missing {', '.join(missing)}", file=sys.stderr)
        return 1
    template = json.loads((NOTIFIER_DIR / f"{args.template}.json").read_text())
    asyncio.run(publish(env, template, dry_run=args.dry_run, id_var=TEMPLATES[args.template]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
