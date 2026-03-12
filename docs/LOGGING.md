# Logging Reference

Module-level loggers via `logging.getLogger(__name__)`. Logger names: `services.parser`, `services.standardizer`, `auth`.

| Event | Level | Module | Notes |
|---|---|---|---|
| Successful parse | `DEBUG` | `services.parser` | `type=` and `country=` |
| Ambiguous parse (RepeatedLabelError) | `WARNING` + `DEBUG` | `services.parser` | WARNING first, then DEBUG with `type=Ambiguous` |
| Standardize call | `DEBUG` | `services.standardizer` | `count=` and `country=` |
| Auth rejection — missing key (401) | `INFO` | `auth` | includes `path=` |
| Auth rejection — invalid key (403) | `INFO` | `auth` | includes `path=` |

Log level controlled by uvicorn `--log-level` (set in systemd unit). `DEBUG` off in production.

New modules: one `getLogger(__name__)` per module; `caplog` assertions in corresponding unit tests.
