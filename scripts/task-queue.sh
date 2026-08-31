#!/usr/bin/env bash
#
# task-queue.sh - the only thing the development key is allowed to run.
#
# The development session polls this queue constantly and unattended. Giving it
# the administrative key for that would put the most powerful credential on the
# machine into the one code path nobody is watching, so instead a dedicated key
# is pinned to this script in ~/.ssh/authorized_keys:
#
#   command="/opt/emma/scripts/task-queue.sh",no-pty,no-port-forwarding,\
#   no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA...
#
# With that line the server runs this script and nothing else, whatever command
# the client asks for. The restriction is enforced by sshd, not by convention.
#
# It follows that this script is the whole attack surface of that key. Two rules
# keep it small:
#
#   * it never accepts SQL, only the fixed verbs below, and builds every query
#     itself;
#   * every value that reaches a query is either checked to be an integer, or
#     matched against a fixed list, or escaped as a string literal.
#
# The worst a stolen development key can therefore do is write nonsense into the
# task queue - which the user reads, so it does not stay hidden - and it cannot
# read .env, touch the service or run anything else.
#
# Usage (through SSH, which passes the request in SSH_ORIGINAL_COMMAND):
#   ssh emma-queue list              # tasks waiting for the developer, as JSON
#   ssh emma-queue list-all          # every open task
#   ssh emma-queue show 3
#   ssh emma-queue touch             # record that the session is alive
#   ssh emma-queue advance 3 implemented "53 test verdi. Committo?"
#   ssh emma-queue finish 3 "deployato"
#   ssh emma-queue abandon 3 "non serve piu'"
#
# It also runs directly, for testing:  scripts/task-queue.sh list
#
# Exit codes: 0 success, 1 refused or failed.

set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname "$(dirname "${SCRIPT_PATH}")")"
ENV_FILE="${PROJECT_DIR}/.env"

die() { printf 'refused: %s\n' "$*" >&2; exit 1; }

command -v sqlite3 >/dev/null || die "sqlite3 is not installed"

# --------------------------------------------------------------------------- #
# Locate the database the same way config.py does: relative paths hang off the
# project directory, absolute ones are taken as they are.
# --------------------------------------------------------------------------- #
read_env() {
    local key="$1" default="$2" value=""
    if [[ -f "${ENV_FILE}" ]]; then
        value="$(sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*(.*)$/\1/p" \
            "${ENV_FILE}" | tail -n 1 | sed -E 's/[[:space:]]+$//')"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
    fi
    printf '%s' "${value:-${default}}"
}

DB="$(read_env MEMORY_DB_PATH data/emma.db)"
[[ "${DB}" == /* ]] || DB="${PROJECT_DIR}/${DB}"
[[ -f "${DB}" ]] || die "no database at ${DB} (has EMMA ever run?)"

# --------------------------------------------------------------------------- #
# The request. Under SSH the client's command line arrives in
# SSH_ORIGINAL_COMMAND because authorized_keys forced this script instead; run
# by hand, it is just the arguments.
# --------------------------------------------------------------------------- #
if [[ -n "${SSH_ORIGINAL_COMMAND:-}" ]]; then
    # Word-split the way a shell would, but without evaluating anything: eval
    # here would hand the restricted key a shell, undoing the entire point.
    read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND}"
    # Everything after the fixed arguments is free text, so recover it verbatim
    # rather than from the split, which would collapse whitespace.
    RAW="${SSH_ORIGINAL_COMMAND}"
else
    ARGS=("$@")
    RAW="$*"
fi

VERB="${ARGS[0]:-}"
[[ -n "${VERB}" ]] || die "no command given"

#: The only stages a task may be moved to. Anything else is rejected before it
#: reaches a query, which is also what keeps the column honest.
VALID_STAGES=(new understood implemented committed pushed deployed)

sql_escape() {
    # Double every single quote: the standard way to put a string into an SQL
    # literal. Nothing else needs escaping inside a quoted SQLite string.
    printf '%s' "${1//\'/\'\'}"
}

require_int() {
    [[ "$1" =~ ^[0-9]+$ ]] || die "'$1' is not a task number"
    printf '%s' "$1"
}

require_stage() {
    local candidate="$1" stage
    for stage in "${VALID_STAGES[@]}"; do
        [[ "${candidate}" == "${stage}" ]] && { printf '%s' "${stage}"; return; }
    done
    die "'${candidate}' is not a stage (${VALID_STAGES[*]})"
}

# Free text is whatever follows the first N words of the request. Taken from the
# raw string so quoting and spacing survive intact.
trailing_text() {
    local skip="$1" text="${RAW}" i
    for ((i = 0; i < skip; i++)); do
        text="${text#"${text%%[![:space:]]*}"}"   # drop leading blanks
        text="${text#"${text%%[[:space:]]*}"}"    # drop one word
    done
    text="${text#"${text%%[![:space:]]*}"}"
    # A note arriving as one quoted argument keeps its quotes here; strip one
    # matching pair so the stored text reads the way it was written.
    if [[ "${text}" == \"*\" && ${#text} -ge 2 ]]; then
        text="${text:1:${#text}-2}"
    fi
    printf '%s' "${text}"
}

query() { sqlite3 -readonly -json "${DB}" "$1"; }
write()  { sqlite3 "${DB}" "$1" >/dev/null; }

# The heartbeat, which EMMA reports to the user as "last seen ...". Any command
# at all proves a session is alive and talking to the queue, so every verb
# records it -- not just the one named after it. Updating it only on `touch`
# would let a session that is busily advancing tasks be reported as dead.
beat() {
    write "INSERT INTO dev_heartbeat(id, last_seen) VALUES(1, strftime('%s','now'))
           ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen"
}

TASK_COLUMNS="id, request, stage, status, note, answer, created_at, updated_at"

VALID_VERBS=(list list-all show touch create advance finish abandon)
case " ${VALID_VERBS[*]} " in
    *" ${VERB} "*) ;;
    *) die "unknown command '${VERB}' (${VALID_VERBS[*]})" ;;
esac
beat

case "${VERB}" in
    list)
        # What the developer has to act on: untouched, or answered and handed
        # back. This is what the watcher polls for.
        query "SELECT ${TASK_COLUMNS} FROM tasks WHERE status = 'queued' ORDER BY id"
        ;;

    list-all)
        query "SELECT ${TASK_COLUMNS} FROM tasks
               WHERE status IN ('queued','waiting_user') ORDER BY id"
        ;;

    show)
        id="$(require_int "${ARGS[1]:-}")"
        query "SELECT ${TASK_COLUMNS} FROM tasks WHERE id = ${id}"
        ;;

    touch)
        # Already recorded above; this verb exists so the watcher can say "still
        # here" during a long quiet stretch without asking for anything.
        echo ok
        ;;

    create)
        # Normally a task is born from the user, through EMMA. This exists for
        # the other case: something found while working on the code, which
        # would otherwise live only in a developer's memory. It changes nothing
        # about who decides - a task opened here still stops at checkpoint 1
        # and asks the user "shall I?" before any of it gets built.
        request="$(trailing_text 1)"
        [[ -n "${request}" ]] || die "create needs a description of the work"
        write "INSERT INTO tasks(created_at, updated_at, request, stage, status)
               VALUES(strftime('%s','now'), strftime('%s','now'),
                      '$(sql_escape "${request}")', 'new', 'queued')"
        sqlite3 "${DB}" "SELECT 'created #' || MAX(id) FROM tasks"
        ;;

    advance)
        id="$(require_int "${ARGS[1]:-}")"
        stage="$(require_stage "${ARGS[2]:-}")"
        note="$(trailing_text 3)"
        [[ -n "${note}" ]] || die "advance needs a note: it is the question the user answers"
        write "UPDATE tasks
               SET stage = '${stage}', note = '$(sql_escape "${note}")', answer = '',
                   status = 'waiting_user', updated_at = strftime('%s','now')
               WHERE id = ${id}"
        echo ok
        ;;

    finish)
        id="$(require_int "${ARGS[1]:-}")"
        note="$(trailing_text 2)"
        write "UPDATE tasks
               SET status = 'done', stage = 'deployed',
                   note = '$(sql_escape "${note}")', updated_at = strftime('%s','now')
               WHERE id = ${id}"
        echo ok
        ;;

    abandon)
        id="$(require_int "${ARGS[1]:-}")"
        note="$(trailing_text 2)"
        write "UPDATE tasks
               SET status = 'abandoned', note = '$(sql_escape "${note}")',
                   updated_at = strftime('%s','now')
               WHERE id = ${id}"
        echo ok
        ;;

    *)
        die "unknown command '${VERB}' (list, list-all, show, touch, advance, finish, abandon)"
        ;;
esac
