#!/usr/bin/env python3
"""Create or update the unit-failure template in notifier (#232).

The wording lives here, in ``infra/notifier/unit-failure.json``; notifier stores
a copy under this tenant. Run once to create it, then again after editing the
JSON to push the change:

    .venv/bin/python infra/publish_notifier_template.py            # create or update
    .venv/bin/python infra/publish_notifier_template.py --dry-run  # render sample only

Reads only ``NOTIFIER_*`` keys from ``--env-file`` (default
``/etc/address-validator/.env``) so the production DSN never enters the process
environment. With ``NOTIFIER_UNIT_FAILURE_TEMPLATE_ID`` set it PATCHes that
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

TEMPLATE = Path(__file__).resolve().parent / "notifier" / "unit-failure.json"
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


async def publish(env: dict[str, str], template: dict[str, Any], *, dry_run: bool) -> str:
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
        template_id = env.get("NOTIFIER_UNIT_FAILURE_TEMPLATE_ID", "").strip()
        if template_id:
            out = await c.templates.update(template_id, **fields)
            print(f"updated template {out.id}")
        else:
            out = await c.templates.create(**fields)
            print(f"created template {out.id}; set NOTIFIER_UNIT_FAILURE_TEMPLATE_ID={out.id}")
        return out.id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true", help="render the sample; write nothing")
    args = parser.parse_args()
    env = read_notifier_env(args.env_file)
    missing = [k for k in ("NOTIFIER_URL", "NOTIFIER_API_KEY") if not env.get(k)]
    if missing:
        print(f"{args.env_file}: missing {', '.join(missing)}", file=sys.stderr)
        return 1
    asyncio.run(publish(env, json.loads(TEMPLATE.read_text()), dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
