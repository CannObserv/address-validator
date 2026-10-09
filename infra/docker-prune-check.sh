#!/bin/bash
# Warn to journal if disk usage on / exceeds 85% after docker prune.
# shellcheck source=infra/journal-warn.sh
source "$(dirname "${BASH_SOURCE[0]}")/journal-warn.sh"
USED=$(df / --output=pcent | tail -1 | tr -d ' %')
[[ "$USED" =~ ^[0-9]+$ ]] || exit 0
if [ "$USED" -ge 85 ]; then
    journal_warn "DISK WARNING: / at ${USED}% after Docker prune"
fi
