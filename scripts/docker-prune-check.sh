#!/bin/bash
# Warn to journal if disk usage on / exceeds 85% after docker prune.
USED=$(df / --output=pcent | tail -1 | tr -d ' %')
[[ "$USED" =~ ^[0-9]+$ ]] || exit 0
if [ "$USED" -ge 85 ]; then
    echo "DISK WARNING: / at ${USED}% after Docker prune" | systemd-cat -t docker-prune -p warning
fi
