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

# Last known state of the queue, kept so that a server which is not answering
# right now does not silently look like an empty queue.
#
# The order matters and is the opposite of what seems obvious. The server is
# asked FIRST and the cache is only a fallback, because the whole point of
# this notice is not missing a job that has just been commissioned -- and a
# cache five minutes old can be missing exactly that. Freshness is the feature;
# the cache buys resilience without spending any of it.
CACHE_FILE="${EMMA_QUEUE_CACHE:-${HOME}/.claude/emma-queue-state}"
CACHE_MAX_AGE="${EMMA_QUEUE_CACHE_MAX_AGE:-86400}"

# The event name reaches Claude Code inside hand-built JSON below, and a stray
# quote there would produce output it cannot parse -- which fails silently,
# the worst way for a notification to fail.
[[ "${EVENT}" == "--refresh" || "${EVENT}" =~ ^[A-Za-z]+$ ]] || exit 0
[[ "${CONNECT_TIMEOUT}" =~ ^[0-9]+$ ]] || CONNECT_TIMEOUT=10

now() { date +%s; }

# Ask the server. Prints the count on success and returns non-zero when the
# queue could not be reached at all -- a distinction the old version could not
# make, because a failed ssh and an empty queue both came out as zero.
ask_the_queue() {
    local listing
    listing="$(ssh -o BatchMode=yes -o ConnectTimeout="${CONNECT_TIMEOUT}" \
        "${QUEUE_HOST}" list 2>/dev/null)" || return 1
    # Count the rows without a JSON parser: neither jq nor a usable python is
    # guaranteed here, and the shape is fixed by scripts/task-queue.sh.
    #
    # The `|| true` is load-bearing. grep exits 1 when it matches nothing, and
    # under `set -o pipefail` that becomes this function's exit status, making
    # an EMPTY queue indistinguishable from a server that never answered --
    # exactly the distinction this function exists to draw. The empty queue
    # would then be served from the cache and announced as work waiting, which
    # is how it behaved when first tested against a queue that had just been
    # cleared.
    local counted
    counted="$(grep -o '"id":' <<< "${listing}" | wc -l | tr -d '[:space:]')" || true
    [[ "${counted}" =~ ^[0-9]+$ ]] || counted=0
    printf '%s' "${counted}"
    return 0
}

write_cache() {
    local dir; dir="$(dirname "${CACHE_FILE}")"
    mkdir -p "${dir}" 2>/dev/null || return 0
    # Written whole and moved into place, so a reader never sees half a file.
    printf 'updated=%s\ncount=%s\n' "$(now)" "$1" > "${CACHE_FILE}.tmp" 2>/dev/null || return 0
    mv -f "${CACHE_FILE}.tmp" "${CACHE_FILE}" 2>/dev/null || rm -f "${CACHE_FILE}.tmp"
}

# Say how long ago in words, because "updated=1756685000" tells a reader
# nothing they can act on.
in_words() {
    local secs="$1"
    if (( secs < 90 )); then echo "meno di un minuto fa"
    elif (( secs < 5400 )); then echo "$(( (secs + 30) / 60 )) minuti fa"
    else echo "$(( (secs + 1800) / 3600 )) ore fa"
    fi
}

count=""
stale=""
if count="$(ask_the_queue)" && [[ "${count}" =~ ^[0-9]+$ ]]; then
    write_cache "${count}"
elif [[ -r "${CACHE_FILE}" ]]; then
    cached_at="$(sed -n 's/^updated=//p' "${CACHE_FILE}" | head -1)"
    cached_count="$(sed -n 's/^count=//p' "${CACHE_FILE}" | head -1)"
    if [[ "${cached_at}" =~ ^[0-9]+$ && "${cached_count}" =~ ^[0-9]+$ ]]; then
        age=$(( $(now) - cached_at ))
        if (( age >= 0 && age <= CACHE_MAX_AGE )); then
            count="${cached_count}"
            stale=" (il server non risponde; dato di $(in_words "${age}"))"
        fi
    fi
fi

# Refresh mode exists for a scheduled task: keep the cache warm, say nothing.
[[ "${EVENT}" == "--refresh" ]] && exit 0

[[ "${count:-}" =~ ^[0-9]+$ ]] || exit 0
(( count > 0 )) || exit 0

if (( count == 1 )); then
    summary="C'e' 1 lavoro di sviluppo commissionato in attesa."
else
    summary="Ci sono ${count} lavori di sviluppo commissionati in attesa."
fi

# additionalContext is what reaches the model; keep it to plain ASCII so the
# hand-built JSON below needs no escaping.
printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s%s Leggili con: ssh %s list"}}\n' \
    "${EVENT}" "${summary}" "${stale}" "${QUEUE_HOST}"
