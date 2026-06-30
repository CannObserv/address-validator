# Architecture

All source modules live under `src/address_validator/`.

## Request flow

```
HTTP request
 └─ middleware/api_version.py  appends API-Version: 2 header on /api/v2/ responses
 └─ middleware/request_id.py  generates ULID, sets ContextVar, echoes X-Request-ID header
 └─ middleware/audit.py       records every API request to audit_log (fire-and-forget)
 └─ routers/v2/               ISO 19160-4 surface; component_profile query param (iso-19160-4 default, usps-pub28, canada-post)
     ├─ parse            →   services/parser.py — US: usaddress wrapper (post-parse recovery in services/parse_recovery.py); CA: libpostal sidecar via LibpostalClient; component_profile controls output key vocabulary
     ├─ standardize      →   services/standardizer/ (package: us.py / ca.py / shared _lines.py) — US: ISO keys via USPS pipeline (Pub 28 abbrev tables from usps_data/); CA: ISO keys via ca.standardize_ca() (canada-post spec); enabled via check_country
     ├─ validate         →   parse → standardize → services/validation/  — US: USPS pipeline; CA raw string: libpostal parse → ca.standardize_ca() → provider; other non-US: components-only
     │                              config.py         pydantic-settings models (USPSConfig, GoogleConfig, ValidationConfig) + validate_config()
     │                              registry.py       ProviderRegistry class — provider lifecycle, quota info, no globals
     │                              null_provider.py  default no-op
     │                              usps_provider.py  OAuth2 + quota guard; DPV → status
     │                              google_provider.py  ADC; lat/lng; DPV → status; non-US via _map_response_international
     │                              chain_provider.py   ordered fallback across providers
     │                              _rate_limit.py      QuotaGuard, QuotaWindow + retry helpers
     └─ countries        →   services/country_format.py  i18naddress → CountryFormatResponseV2; label lookup tables
 └─ routers/deps.py            shared FastAPI dependency functions — get_registry() → ProviderRegistry; get_libpostal_client() → LibpostalClient | None
 └─ routers/admin/            admin dashboard (Jinja2 + HTMX, exe.dev auth)
     ├─ router.py             top-level /admin router
     ├─ deps.py               AdminUser from exe.dev proxy headers
     ├─ _config.py            shared templates, CSS version, quota helpers
     ├─ _sparkline.py         inline SVG sparkline builder (colors, trend labels)
     ├─ dashboard.py          GET /admin/ — landing page
     ├─ audit_views.py        GET /admin/audit/ — audit log with filters
     ├─ endpoints.py          GET /admin/endpoints/{name}
     ├─ providers.py          GET /admin/providers/{name}
     ├─ candidates.py         GET /admin/candidates/ (list, grouped by raw_address); GET /{raw_hash} (detail); POST /{raw_hash}/status, /notes (HTMX triage actions); POST /{raw_hash}/batches (assign), /{raw_hash}/batches/{slug}/unassign
     ├─ batches.py            GET /admin/batches/ (list, filter by status); GET /{slug} (detail); POST / (plan new); POST /{slug}/status (lifecycle transition)
     ├─ partials.py           GET /admin/_partials/* — small lazy-loaded HTMX fragments (e.g. nav badges) injected into the shared admin layout
     └─ queries/              SQLAlchemy Core query helpers for dashboard views
         ├─ _shared.py        shared expressions, helpers, and time boundaries; is_error_expr / is_rate_limited_expr (429 is not an error)
         ├─ audit.py          get_audit_rows
         ├─ candidates.py     get_candidate_groups, get_candidate_group, get_candidate_submissions, get_new_candidate_count, update_candidate_status, update_candidate_notes; WRITE_STATUSES={'new','rejected'} frozenset; rows with status='labeled' are excluded from the triage view; group queries surface batch_slugs via LEFT JOIN to candidate_batch_assignments
         ├─ batches.py        list_batches, get_batch_by_slug, get_assignable_batches, get_batch_candidates
         ├─ dashboard.py      get_dashboard_stats, get_sparkline_data
         ├─ endpoint.py       get_endpoint_stats
         └─ provider.py       get_provider_stats, get_provider_daily_usage (audit-derived requests today, per provider)
```

## Key modules

```
db/tables.py        SQLAlchemy Core Table definitions (audit_log, audit_daily_stats, model_training_candidates, training_batches, candidate_batch_assignments)
db/engine.py        AsyncEngine singleton — init_engine(), get_engine(), close_engine(), Alembic migrations
models.py           API contract source of truth; StandardizedAddress = StandardizeResponseV2 type alias — use StandardizedAddress in service/provider code for version-neutral typing, StandardizeResponseV2 as the public response model on /api/v2/standardize
core/address_format.py  build_validated_string — canonical single-line address string builder; shared across validation providers and the router layer
core/countries.py  SUPPORTED_COUNTRIES (US+CA), VALID_ISO2 frozensets; check_country() — canonical home for country validation used by all v2 routes
core/errors.py     APIError exception class; api_error_response() — serialises APIError to JSONResponse; registered in main.py exception handler; imported by all router layers
core/warnings.py   single source of truth for response `warnings` strings (static constants + str.format templates + CATALOGUE tuple); catalogued in docs/WARNINGS.md, kept in sync by tests/unit/test_warnings_catalogue.py
services/spec.py                 ISO 19160-4 spec identifiers (ISO_19160_4_SPEC, ISO_19160_4_SPEC_VERSION); used by v2 routers; USPS Pub 28 identifiers remain in usps_data/spec.py
services/component_profiles.py  ISO 19160-4 ↔ USPS Pub28 key translation; translate_components() / translate_components_to_iso(); VALID_PROFILES frozenset; identity pass-through for unknown profiles/keys
services/validation/pipeline.py  parse → standardize → provider-selection pipeline; build_non_us_std() (shared passthrough std for non-US components), run_us_pipeline() (US path, accepts component_profile param), run_non_us_pipeline() (CA raw strings via libpostal, other non-US via components-only); all return (std, raw_input, provider); raises APIError on validation failures
services/libpostal_client.py  async httpx client for pelias/libpostal-service (port 4400); maps libpostal tags → ISO 19160-4; LibpostalUnavailableError on failure; aclose() in lifespan
services/street_splitter.py  bilingual street component splitter; decomposes libpostal road token into thoroughfare ISO elements; English trailing-type + French leading-type + CA directionals
canada_post_data/directionals.py  bilingual EN/FR directional lookup (CA_DIRECTIONAL_MAP) for Canadian addresses; used by street_splitter
canada_post_data/provinces.py  Canada Post province/territory table (PROVINCE_MAP): full names + abbreviations → 2-letter abbreviation; used by _standardize_ca()
canada_post_data/suffixes.py   Canada Post street type table (CA_SUFFIX_MAP): bilingual EN/FR suffix → standard abbreviation; used by _standardize_ca()
canada_post_data/spec.py       CANADA_POST_SPEC / CANADA_POST_SPEC_VERSION — tags CA ComponentSet responses; spec="canada-post", spec_version="2025"
services/country_format.py  maps i18naddress ValidationRules → CountryFormatResponseV2; backs GET /api/v2/countries/{code}/format
services/audit.py   audit ContextVars + write_audit_row (fail-open DB insert)
services/training_candidates.py  training ContextVars + write_training_candidate (fail-open DB insert); records endpoint/provider/api_version/failure_reason denormalised onto each row
services/training_batches.py    batch lifecycle — ALLOWED_TRANSITIONS state machine + CRUD (create_batch, transition_status, advance_step, assign_candidates, unassign_candidates, get_batch_id_by_slug, record_upstream_pr); admin routes AND scripts/model/*.py call through this for all status transitions
usps_data/          Pub 28 lookup tables (suffixes, directionals, states, units)
usps_data/spec.py   USPS_PUB28_SPEC* — tags every ComponentSet response
logging_filter.py   RequestIdFilter — injects request_id into every LogRecord via root logger
templates/admin/    Jinja2 templates (base, dashboard, audit, endpoints, providers); _thead.html + _rows.html shared partials
static/admin/css/   Tailwind CSS (input.css + built tailwind.css)
static/admin/js/    ES modules — theme.js (dark mode), nav.js (hamburger)
tests/js/           Vitest + jsdom tests for admin JS (npm run test:js)
package.json        Node dev-only deps (vitest, jsdom); type: "module"
vitest.config.js    Vitest config — jsdom environment, tests/js/ scope
static/admin/images/ Cannabis Observer brand SVGs

scripts/db/          DB maintenance + one-time migration scripts (backfill_audit_log, backfill_pattern_key, backfill_audit_raw_input, migrate_sqlite_to_postgres)
scripts/model/       Training pipeline scripts (identify, label, train, test_model, deploy, performance, contribute)
skills/train-model/  /train-model skill — interactive 7-step pipeline orchestration
training/batches/    Per-batch training artifacts (timestamped dirs)
training/upstream/   Upstream usaddress training data (labeled.xml)
```
