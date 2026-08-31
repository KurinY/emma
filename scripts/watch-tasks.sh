#!/usr/bin/env bash
#
# watch-tasks.sh - wait until there is development work, then get out of the way.
#
# Run on the development machine, in the background, by the session that does
# the work:
#
#   scripts/watch-tasks.sh
#
# It polls the queue and exits as soon as something is waiting. That is the
# whole design: the waiting is done by this script, which costs nothing, and the
# session that costs something only wakes up when there is actually a task. A
# quiet day is a day of sleeping shell, not a day of empty check-ins.
#
# Exit codes:
#   0  there is work; the JSON of the queued tasks is on stdout
#   1  could not reach the queue (see stderr)
#   2  gave up after MAX_WAIT_SECONDS with nothing to do
#
# Configuration, all through the environment:
#
#   EMMA_QUEUE_HOST     ssh destination                  (default: emma-queue)
#   POLL_SECONDS        seconds between checks           (default: 300)
#   MAX_WAIT_SECONDS    give up after this long          (default: 21600, 6h)
#
# EMMA_QUEUE_HOST is deliberately a name and not an address. Put the real
# destination in ~/.ssh/config, which is not in this repository:
#
#   Host emma-queue
#       HostName <the server>
#       User emma
#       IdentityFile ~/.ssh/emma_queue
#       IdentitiesOnly yes
#
# That key is the restricted one, pinned on the server to scripts/task-queue.sh.
# It is not the administrative key: this loop runs unattended, and the whole
# point is that the credential it holds cannot do anything but read and update
# the queue.

set -uo pipefail

QUEUE_HOST="${EMMA_QUEUE_HOST:-emma-queue}"
POLL_SECONDS="${POLL_SECONDS:-300}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-21600}"

[[ "${POLL_SECONDS}" =~ ^[0-9]+$ && "${POLL_SECONDS}" -ge 1 ]] \
    || { echo "POLL_SECONDS must be a positive integer" >&2; exit 1; }

log() { printf '%s | %s\n' "$(date -Is)" "$*" >&2; }

# A short timeout and no interactivity: this runs unattended, so a prompt or a
# hung connection would stall the loop instead of failing it.
ssh_queue() {
    ssh -o BatchMode=yes -o ConnectTimeout=15 "${QUEUE_HOST}" "$@"
}

# Fail fast on a destination that is not reachable or not configured, rather
# than looping quietly for hours against nothing.
if ! ssh_queue touch >/dev/null 2>&1; then
    log "cannot reach the queue at '${QUEUE_HOST}'."
    log "check the Host entry in ~/.ssh/config and that the key is installed."
    exit 1
fi

log "watching '${QUEUE_HOST}' every ${POLL_SECONDS}s (up to ${MAX_WAIT_SECONDS}s)"

WAITED=0
while true; do
    # touch first: the heartbeat is what tells the user, through EMMA, that this
    # session is still alive. A watcher that checked without recording it would
    # look identical to no watcher at all.
    ssh_queue touch >/dev/null 2>&1 || log "warning: heartbeat failed, continuing"

    if QUEUED="$(ssh_queue list 2>/dev/null)"; then
        # sqlite3 -json prints an empty array, or nothing at all, when there are
        # no rows; anything else means there is at least one task.
        case "${QUEUED//[[:space:]]/}" in
            ""|"[]") ;;
            *)
                log "work waiting - waking the session"
                printf '%s\n' "${QUEUED}"
                exit 0
                ;;
        esac
    else
        log "warning: could not read the queue, retrying"
    fi

    if (( WAITED >= MAX_WAIT_SECONDS )); then
        log "nothing to do after ${WAITED}s, stopping"
        exit 2
    fi

    sleep "${POLL_SECONDS}"
    WAITED=$(( WAITED + POLL_SECONDS ))
done
