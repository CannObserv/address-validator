# USPS spend ceiling — design (short)

**Issue:** GH #404 · **Date:** 2026-11-05 · **Size:** small change (settings + docs)

## Intent

USPS bills the Addresses API by monthly call volume. Our volume was sized for quota, not cost: the
licensing tracker's daily renewal run (wslcb-licensing-tracker#195, live 2026-10-27) put November
on pace for ~7,300 calls (~$36). Its steady state would cost $47–55 a month. September's initial
bulk run cost $220.

**Operator decision:** roughly 160 USPS calls a day, a bill **under $25/month** (≤ 5,000 calls), and
the tracker's refresh cycles stretched as far as needed.

## Goals / non-goals / anti-goals

- **Goal:** every monthly debit ≤ $22.50, starting with November's (debited ~Dec 1).
- **Non-goal:** a restart-proof cap, a monthly budget window, or dashboard spend visibility (Options 2 and 3).
- **Anti-goal:** tracker renewals silently degrading into Google answers with no DPV code because
  our cap sits below the tracker's.

## Constraints

- Fee tiers (Tech Sheet 2026-08-13): 1–2,000 → $10; then $4.50 / $4.25 / $4.00 per 1,000. The whole month bills at one tier; partial thousands round up.
- `QuotaWindow` is in-memory and starts full on boot.
- With `usps,google`, a call over the USPS cap falls through to Google (160/day), then to a 429.

## Approaches

1. **Settings only, both repos** — *picked by the operator.* Smallest change. Our cap is a leaky
   backstop; the tracker's own budget holds the bill.
2. Option 1, plus seeding the USPS daily window from `audit_log` at boot, the way Google's window is
   seeded from Cloud Monitoring. Restart-proof; a small code change.
3. Option 2, plus a calendar-month USPS budget and month-to-date spend on the admin dashboard. Needs
   a durable call counter (a migration), so it's a full project.

## Decided

- **address-validator:** `USPS_DAILY_LIMIT` 500 → **140** in production, then restart.
  - *My call, inside the operator's "~160":* 140 and 160 bill the same $22.50 in any month (4,340 and
    4,960 calls are both 5 blocks of 1,000), and only 140 fits November: 1,232 by 11-05 + 25 × 140 ≈ 4,732.
- **address-validator:** `docs/VALIDATION-PROVIDERS.md` records the fee schedule, how to read the EPS
  bill, and why production runs 140.
- **wslcb-licensing-tracker** (its own issue, through the operator). Proposed: `DAILY_VALIDATION_LIMIT`
  450 → 120, `RENEWAL_RESERVE` 90 → 40, TTLs 180/365/365 → 730/1,460/1,460, and
  `NO_DETERMINATION_DAILY_LIMIT` 40 → 10. Steady state ≈ 107/day. The tracker's implementer checks
  these against #195's projection and owns the final numbers.

## Left to the builder

- **Order of rollout:** our cap first, because the bill is accruing. Until the tracker's cap drops,
  expect fall-through to Google and some tracker retry-laters; that's acceptable for days, not weeks.
- **Verification:**
  - The running process's environment has `USPS_DAILY_LIMIT=140`. Startup doesn't log the limit, and
    health doesn't report it. Check with
    `sudo grep -z '^USPS_DAILY_LIMIT=' /proc/$(systemctl show -p MainPID --value address-validator)/environ`.
    MainPID is the uvicorn process itself; it has no worker children.
  - USPS-path calls per UTC day (the `audit_log` query in #404) stay ≤ 140 between restarts.
  - The Dec 1 EPS debit is ≤ $22.50.
