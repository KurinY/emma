#!/usr/bin/env bash
#
# queue-brief.sh - say whether commissioned work is waiting, for a Claude Code hook.
#
# Without this, a development session opens knowing nothing about the queue, and
# a request left overnight sits there until somebody thinks to look. The whole
# arrangement has no service behind it (REVISIONE.md, entry 17.8), so noticing
# is a manual act unless something makes it automatic.
#
# It was written for SessionStart alone, which turned out to cover only the
# easiest case. A session that starts is told; a session already running is
# not, however long it runs. On 1 September 2026 a job was commissioned during
# a session that had been open for hours, and nothing existed that could have
# mentioned it. The event name is now an argument so the same check can run
# wherever it is useful -- see REVISIONE.md, entry 23.
#
# It reports a count, deliberately, and not the requests themselves: the session
# can read them with `ssh emma-queue list` when it acts on them, and injecting
# their text into every start would spend context on work that may not be
# touched this session.
#
# Usage:
#
#   queue-brief.sh [EVENT_NAME] [CONNECT_TIMEOUT_SECONDS]
#
# EVENT_NAME must match the hook it is wired into -- Claude Code discards
# output whose hookEventName does not -- and defaults to SessionStart.
#
# The timeout is the second argument because the two callers want different
# ones. At session start, ten seconds spent finding out is free; on every user
# message it is ten seconds of somebody waiting, so that caller passes less.
#
# Wire it into .claude/settings.local.json:
#
#   { "hooks": { "SessionStart": [ { "hooks": [ {
#       "type": "command",
#       "command": "<path to this script> SessionStart 10"
#   } ] } ] } }
#
# It always exits 0 and prints nothing when the queue is empty or unreachable.
# A session must never fail to start, and a message must never fail to send,
# because a server is down.

set -uo pipefail

QUEUE_HOST="${EMMA_QUEUE_HOST:-emma-queue}"
EVENT="${1:-SessionStart}"
CONNECT_TIMEOUT="${2:-10}"

# The event name reaches Claude Code inside hand-built JSON below, and a stray
# quote there would produce output it cannot parse -- which fails silently,
# the worst way for a notification to fail.
[[ "${EVENT}" =~ ^[A-Za-z]+$ ]] || exit 0
[[ "${CONNECT_TIMEOUT}" =~ ^[0-9]+$ ]] || CONNECT_TIMEOUT=10

# Count the rows in the JSON array without a JSON parser: neither jq nor a
# usable python is guaranteed on this machine, and the shape is fixed because
# scripts/task-queue.sh is what produces it.
count="$(
    ssh -o BatchMode=yes -o ConnectTimeout="${CONNECT_TIMEOUT}" "${QUEUE_HOST}" list 2>/dev/null \
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
printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s Leggili con: ssh %s list"}}\n' \
    "${EVENT}" "${summary}" "${QUEUE_HOST}"
