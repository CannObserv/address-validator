# USPS Enhanced Addresses API switch — design

**Date:** 2026-07-02 · **Deadline:** 2026-07-12 (USPS-imposed)

## Problem

Effective 2026-07-12, USPS closes the Addresses API to unlicensed access:

- Users must sign the **Addressing API License Agreement**
  ([PostalPro](https://postalpro.usps.com/Addressing_API_License), final dated
  2026-04-08), execute an **Order Form**, hold a USPS COP account and an
  **Enterprise Payment System (EPS)** account, and pay consumption tier-based
  fees. Pricing unpublished — via order form only.
- USPS launches the **Enhanced Addresses API** the same day: daily AMD data
  updates, improved matching, new indicators (delivery type, usage code,
  seasonal/LACS/PBSA/throwback attributes, non-delivery days), and a
  webhooks/subscriptions API
  (`developers.usps.com/subscriptions-addressesv3`).
- New endpoint URLs are **not yet published** — the "Enhanced Addresses 3.0"
  portal page (`developers.usps.com/addressdetailsv3`) has no spec. Behavior
  of existing OAuth creds on 2026-07-12 is undocumented.

Source: USPS Industry Alert 2026-06-01 (2nd reminder),
[PostalPro node 15211](https://postalpro.usps.com/node/15211).

## Spec delta (r2_3 → r2_4)

Diffed `addresses-v3r2_4.yaml` against vendored r2_3: version 3.2.2→3.2.3,
doc-example fixes only (examples moved 200→404 block, `apiVersion` labels
v1→v3). **Same servers, paths, params, schemas.** `usps_client.py` needs no
change for r2_4.

## Exposure

Production: `VALIDATION_PROVIDER=usps,google`. If USPS creds lapse 07-12,
401/403 → `ProviderBadRequestError` → chain falls through to Google
per-request (service up, extra latency, USPS-grade standardization lost).

## Decisions (interview 2026-07-02)

| Question | Decision |
|---|---|
| License status | In progress (COP/EPS/order form underway, not executed) |
| Gap behavior | Config flip to `VALIDATION_PROVIDER=google`; runbook in `docs/VALIDATION-PROVIDERS.md`. No auto-degrade code. |
| Feature scope | **Continuity only.** New indicators/webhooks noted on GH #122 (recon epic) for a future design once spec publishes. |
| Detection | Scheduled canary agent, daily 07-10 → 07-20 |
| Client prep | **Approach A** — env-overridable USPS base URLs, prod defaults unchanged |

## Design

### 1. Vendored spec
`docs/usps-addresses-v3r2_3.yaml` → `docs/usps-addresses-v3r2_4.yaml`;
references updated in `docs/usps-pub28.md`, `docs/VALIDATION-PROVIDERS.md`.
(Done in this commit.)

### 2. Base-URL config (code change — worktree + PR)
- New env vars, both optional:
  - `USPS_API_BASE` — default `https://apis.usps.com`
  - Token URL derived: `{base}/oauth2/v3/token`; address URL:
    `{base}/addresses/v3/address`
- Single base var (not per-endpoint) — TEM mirrors the full path structure
  (`apis-tem.usps.com`); if Enhanced API changes path shapes, revisit then.
- `USPSConfig` in `services/validation/config.py` + thread through
  `registry.py` → `USPSClient.__init__`; module constants become fallbacks.
- Tests: config parsing, client uses injected base. No parse/standardize
  output change → **no `PIPELINE_CODE_VERSION` bump**.
- Document in `docs/VALIDATION-PROVIDERS.md` env table.

### 3. Gap runbook
`docs/VALIDATION-PROVIDERS.md` § "Enhanced Addresses API switch — gap
runbook": flip to google-only, verify, token-probe, restore. (Done in this
commit.)

### 4. Canary (scheduled agent, no production code)
Daily 07-10 → 07-20:
- OAuth token probe against prod creds
- One live validation call through the service path
- Re-fetch spec YAML (checksum vs vendored r2_4) + portal page for
  endpoint/licensing drift
- Report failures/drift; on cred lapse → execute runbook step 1

### 5. GH tracking
- Tracking issue: business checklist (license, order form, EPS — operator),
  technical checklist (items 1–4), links.
- Comment on #122: Enhanced API indicator list as future recon targets.

## Out of scope (YAGNI)

- Webhooks/subscriptions integration — no spec, unclear fit
- Auto-degrade/circuit-breaker on 401/403 — config flip suffices
- New indicator surfacing in `ValidationResult` — blocked on published spec;
  recon logging (#122) already detects new response fields
- Per-endpoint URL overrides — single base var covers known cases

## Risks

- **Endpoints change 07-12 with no published spec** → base-URL env flip
  covers host change; path-shape change needs a small PR (client is ~1 file).
- **License not executed by 07-12** → runbook; Google daily quota (160/day)
  becomes binding limit during gap.
- **Fees/tier unknown** → operator reviews order form; `USPS_DAILY_LIMIT`
  may need lowering to fit purchased tier.
