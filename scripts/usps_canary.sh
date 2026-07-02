#!/usr/bin/env bash
# USPS Enhanced Addresses API switch canary — GH #155.
#
# Daily crontab probe for the 2026-07-12 licensing switch (window Jul 10-20):
#   23 14 10-20 7 * /home/exedev/address-validator/scripts/usps_canary.sh
#
# Probes (all bypass the service cache; nothing is written to the prod DB):
#   1. OAuth2 token endpoint with production creds
#   2. Direct GET /addresses/v3/address with that token (fixed USPS HQ address)
#   3. Published addresses-v3r2_4.yaml vs vendored docs copy (checksum)
#   4. Published enhanced-addresses-v3r2.yaml vs vendored docs copy (checksum)
#   5. Developer-portal pages for spec files beyond the two known ones
#
# Logs to scratch/usps-canary.log (gitignored). Comments on GH #155 when any
# probe fails or drifts — and unconditionally on July 12 (switch day).
# Exits 1 when any probe recorded an anomaly, 0 otherwise.
set -u

REPO="/home/exedev/address-validator"
PROD_ENV="/etc/address-validator/.env"
LOG="$REPO/scratch/usps-canary.log"
SPEC_BASE="https://developers.usps.com/sites/default/files/apidoc_specs"
ANOMALIES=()

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
note() { echo "$(ts) $*" >>"$LOG"; }
getvar() { grep "^$1=" "$2" | head -1 | cut -d= -f2-; }

# All temp files are registered in TMPFILES (in the parent shell — an append
# inside $(...) would land in a subshell and be lost) and cleaned on any exit.
# Signals are converted to exits because bash does not run the EXIT trap when
# killed by a default-disposition signal.
TMPFILES=()
trap 'rm -f "${TMPFILES[@]}"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$(dirname "$LOG")"
note "--- canary run"

# 1. OAuth token probe — creds written (no trailing newline; mktemp mode 600)
# to temp files read via --data-urlencode @file, so they never appear in
# process argv and no shell-quoting of their content is involved.
KEY_FILE=$(mktemp /tmp/usps_canary_key.XXXXXX) && TMPFILES+=("$KEY_FILE")
SECRET_FILE=$(mktemp /tmp/usps_canary_secret.XXXXXX) && TMPFILES+=("$SECRET_FILE")
printf '%s' "$(getvar USPS_CONSUMER_KEY "$PROD_ENV")" >"$KEY_FILE"
printf '%s' "$(getvar USPS_CONSUMER_SECRET "$PROD_ENV")" >"$SECRET_FILE"
TOKEN=$(curl -s -m 20 -X POST https://apis.usps.com/oauth2/v3/token \
  -d grant_type=client_credentials \
  --data-urlencode "client_id@$KEY_FILE" \
  --data-urlencode "client_secret@$SECRET_FILE" |
  python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)
rm -f "$KEY_FILE" "$SECRET_FILE"
if [ -n "$TOKEN" ]; then
  note "oauth: ok"
else
  note "oauth: FAIL"
  ANOMALIES+=("OAuth token probe FAILED — creds rejected or endpoint unreachable (see gap runbook in docs/VALIDATION-PROVIDERS.md)")
fi

# 2. Direct address validation (USPS HQ; public address, no PII)
if [ -n "$TOKEN" ]; then
  BODY=$(mktemp /tmp/usps_canary_addr.XXXXXX) && TMPFILES+=("$BODY")
  HTTP=$(curl -s -m 20 -o "$BODY" -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    "https://apis.usps.com/addresses/v3/address?streetAddress=475%20LENFANT%20PLZ%20SW&city=WASHINGTON&state=DC")
  DPV=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("additionalInfo",{}).get("DPVConfirmation",""))' "$BODY" 2>/dev/null)
  if [ "$HTTP" = "200" ] && [ -n "$DPV" ]; then
    note "address: ok (dpv=$DPV)"
  else
    note "address: FAIL (http=$HTTP dpv=${DPV:-none})"
    ANOMALIES+=("Address validation probe FAILED (HTTP $HTTP, DPVConfirmation=${DPV:-none})")
  fi
  rm -f "$BODY"
fi

# 3+4. Spec drift vs vendored copies
for SPEC in addresses-v3r2_4:usps-addresses-v3r2_4 enhanced-addresses-v3r2:usps-enhanced-addresses-v3r2; do
  REMOTE="${SPEC%%:*}.yaml"
  LOCAL="$REPO/docs/${SPEC##*:}.yaml"
  TMP=$(mktemp /tmp/usps_canary_spec.XXXXXX) && TMPFILES+=("$TMP")
  if curl -s -m 20 -o "$TMP" "$SPEC_BASE/$REMOTE" && [ -s "$TMP" ] && head -1 "$TMP" | grep -q openapi; then
    if cmp -s "$TMP" "$LOCAL"; then
      note "spec $REMOTE: ok"
    else
      note "spec $REMOTE: DRIFT"
      ANOMALIES+=("Published $REMOTE differs from vendored $(basename "$LOCAL") — re-vendor and re-review")
    fi
  else
    note "spec $REMOTE: fetch failed"
    ANOMALIES+=("Could not fetch $SPEC_BASE/$REMOTE (removed or moved?)")
  fi
  rm -f "$TMP"
done

# 5. Portal pages — spec files beyond the known two
NEW_LINKS=$(curl -sL -m 20 https://developers.usps.com/addressesv3 https://developers.usps.com/addressdetailsv3 2>/dev/null |
  grep -oE 'apidoc_specs/[A-Za-z0-9._-]+\.(yaml|json)' | sort -u |
  grep -v -e 'addresses-v3r2_4\.yaml' -e 'enhanced-addresses-v3r2\.yaml' || true)
if [ -n "$NEW_LINKS" ]; then
  note "portal: new spec links: $(echo "$NEW_LINKS" | tr '\n' ' ')"
  ANOMALIES+=("New spec file(s) on developer portal: $(echo "$NEW_LINKS" | tr '\n' ' ')")
else
  note "portal: ok"
fi

# Report — on any anomaly, and unconditionally on switch day
if [ ${#ANOMALIES[@]} -gt 0 ] || [ "$(date -u +%m-%d)" = "07-12" ]; then
  GH_TOKEN=$(getvar GH_TOKEN "$REPO/.env")
  export GH_TOKEN
  {
    echo "### USPS switch canary — $(ts)"
    if [ ${#ANOMALIES[@]} -eq 0 ]; then
      echo "Switch-day status: all probes OK (oauth, address, spec x2, portal)."
    else
      for a in "${ANOMALIES[@]}"; do echo "- :warning: $a"; done
      echo
      echo 'Log: `scratch/usps-canary.log` on the VM. Gap runbook: `docs/VALIDATION-PROVIDERS.md`.'
    fi
  } | gh --repo CannObserv/address-validator issue comment 155 --body-file - >/dev/null &&
    note "report: commented on #155" || note "report: gh comment FAILED"
fi

[ ${#ANOMALIES[@]} -eq 0 ]
