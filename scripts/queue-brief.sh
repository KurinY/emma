#!/usr/bin/env bash
#
# queue-brief.sh - say whether commissioned work is waiting, for a SessionStart hook.
#
# Without this, a development session opens knowing nothing about the queue, and
# a request left overnight sits there until somebody thinks to look. The whole
# arrangement has no service behind it (REVISIONE.md, entry 17.8), so noticing
# is a manual act unless something makes it automatic.
#
# It reports a count, deliberately, and not the requests themselves: the session
# can read them with `ssh emma-queue list` when it acts on them, and injecting
# their text into every start would spend context on work that may not be
# touched this session.
#
# Wire it into .claude/settings.local.json:
#
#   { "hooks": { "SessionStart": [ { "hooks": [ {
#       "type": "command",
#       "command": "<path to this script>"
#   } ] } ] } }
#
# It always exits 0 and prints nothing when the queue is empty or unreachable.
# A session must never fail to start because a backup server is down.

set -uo pipefail

QUEUE_HOST="${EMMA_QUEUE_HOST:-emma-queue}"

# Count the rows in the JSON array without a JSON parser: neither jq nor a
# usable python is guaranteed on this machine, and the shape is fixed because
# scripts/task-queue.sh is what produces it.
count="$(
    ssh -o BatchMode=yes -o ConnectTimeout=10 "${QUEUE_HOST}" list 2>/dev/null \
        | grep -o '"id":' \
        | wc -l \
        | tr -d '[:space:]'
)"

[[ "${count:-0}" =~ ^[0-9]+$ ]] || exit 0
(( count > 0 )) || exit 0

if (( count == 1 )); then
    summary="C'e' 1 lavoro di sviluppo commissionato in attesa."
else
    summary="Ci sono ${count} lavori di sviluppo commissionati in attesa."
fi

# additionalContext is what reaches the model; keep it to plain ASCII so the
# hand-built JSON below needs no escaping.
printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s Leggili con: ssh %s list"}}\n' \
    "${summary}" "${QUEUE_HOST}"
