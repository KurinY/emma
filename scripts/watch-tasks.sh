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
# Exit codes, in the default mode:
#   0  there is work; the JSON of the queued tasks is on stdout
#   1  could not reach the queue (see stderr)
#   2  gave up after MAX_WAIT_SECONDS with nothing to do
#
# Exit codes in hook mode (EMMA_WAKE_ON_WORK=1), which are deliberately not
# the same:
#   2  there is work that has not been announced yet -- Claude Code reads this
#      as "wake the model", which is the whole point of the mode
#   0  everything else, including having given up and not having reached the
#      queue. In hook mode a wake-up is a claim that something is waiting, so
#      it must never be spent on a network failure or on a quiet six hours.
#
# The two meanings of 2 are opposite, which is exactly why the mode is
# explicit rather than inferred: an asyncRewake hook wired to the default
# codes would wake the session precisely when there was nothing to say.
#
# Configuration, all through the environment:
#
#   EMMA_QUEUE_HOST     ssh destination                  (default: emma-queue)
#   POLL_SECONDS        seconds between checks           (default: 300)
#   MAX_WAIT_SECONDS    give up after this long          (default: 21600, 6h)
#   EMMA_WAKE_ON_WORK   1 to use the hook codes above    (default: unset)
#   EMMA_SEEN_FILE      where announced ids are recorded (hook mode only)
#   EMMA_LOCK_FILE      where the running instance is noted (hook mode only)
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
WAKE_ON_WORK="${EMMA_WAKE_ON_WORK:-0}"
SEEN_FILE="${EMMA_SEEN_FILE:-${TMPDIR:-/tmp}/emma-watch-seen}"
LOCK_FILE="${EMMA_LOCK_FILE:-${TMPDIR:-/tmp}/emma-watch.lock}"

[[ "${POLL_SECONDS}" =~ ^[0-9]+$ && "${POLL_SECONDS}" -ge 1 ]] \
    || { echo "POLL_SECONDS must be a positive integer" >&2; exit 1; }

log() { printf '%s | %s\n' "$(date -Is)" "$*" >&2; }

# Which task ids this watcher has already woken somebody about.
#
# Without it the arrangement eats itself: the watcher wakes the session, a
# hook starts it again, the same job is still queued, and it wakes the session
# again -- for as long as the job stays open, which for a job waiting on an
# answer is indefinitely. A wake-up has to mean "something you have not seen",
# not "something exists".
remember() { printf '%s\n' "$@" >> "${SEEN_FILE}"; }
already_seen() { [[ -f "${SEEN_FILE}" ]] && grep -qxF "$1" "${SEEN_FILE}"; }

# The ids in a queue listing, without a JSON parser: neither jq nor a usable
# python is guaranteed here, and the shape is fixed by scripts/task-queue.sh.
ids_in() { grep -o '"id":[0-9]*' <<< "$1" | grep -o '[0-9]*'; }

# In hook mode a wake-up is a claim that something is waiting. Anything else --
# a lost connection, a quiet six hours -- exits 0 and says nothing, because a
# notification that cries wolf is one you learn to ignore.
finish() {
    local reason="$1" default_code="$2"
    if [[ "${WAKE_ON_WORK}" == "1" ]]; then
        log "${reason}"
        exit 0
    fi
    exit "${default_code}"
}

# One watcher at a time.
#
# The Stop hook re-arms this after every turn, which is what keeps it running
# for as long as the session is open -- and would also start a second, and a
# tenth, each polling the same queue and each holding an ssh connection. The
# lock is what makes re-arming idempotent: asking for a watcher when one is
# already watching does nothing at all.
#
# A lock left behind by a killed process must not block the next one forever,
# so the recorded pid is checked rather than trusted.
claim_the_lock() {
    if [[ -f "${LOCK_FILE}" ]]; then
        local owner
        owner="$(head -1 "${LOCK_FILE}" 2>/dev/null || true)"
        if [[ "${owner}" =~ ^[0-9]+$ ]] && kill -0 "${owner}" 2>/dev/null; then
            return 1
        fi
        log "clearing a lock left by pid ${owner:-?}, which is gone"
    fi
    printf '%s\n' "$$" > "${LOCK_FILE}" 2>/dev/null || return 0
    # Only the holder removes it, so a loser of the race cannot free the winner.
    trap 'rm -f "${LOCK_FILE}"' EXIT
    return 0
}

if [[ "${WAKE_ON_WORK}" == "1" ]] && ! claim_the_lock; then
    log "another watcher is already running; nothing to do"
    exit 0
fi

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
    finish "not watching: the queue is unreachable" 1
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
                if [[ "${WAKE_ON_WORK}" != "1" ]]; then
                    log "work waiting - waking the session"
                    printf '%s\n' "${QUEUED}"
                    exit 0
                fi

                # Hook mode. Only what has not been announced before is worth
                # a wake-up; anything already announced leaves the loop to go
                # on waiting, rather than reporting the same job forever.
                #
                # Deliberately not `break` here: `case` is not a loop, so a
                # break would leave the `while` and end the watcher instead of
                # continuing it -- which it did, silently, until a test asked
                # why the watcher kept dying five seconds in.
                fresh=()
                while read -r id; do
                    already_seen "${id}" || fresh+=("${id}")
                done < <(ids_in "${QUEUED}")

                if (( ${#fresh[@]} > 0 )); then
                    remember "${fresh[@]}"
                    log "new work (#$(IFS=,; echo "${fresh[*]}")) - waking the session"
                    printf '%s\n' "${QUEUED}"
                    exit 2
                fi
                ;;
        esac
    else
        log "warning: could not read the queue, retrying"
    fi

    if (( WAITED >= MAX_WAIT_SECONDS )); then
        finish "nothing to do after ${WAITED}s, stopping" 2
    fi

    sleep "${POLL_SECONDS}"
    WAITED=$(( WAITED + POLL_SECONDS ))
done
