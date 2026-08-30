#!/usr/bin/env bash
#
# backup.sh - dated, rotated backup of a EMMA installation.
#
# What ends up in the archive:
#   * the whole project directory at its current version (code, prompts,
#     systemd units, docs), minus caches and the virtual environment;
#   * the .env file, secrets included - which is why the destination is locked
#     down to the owner;
#   * a MANIFEST.txt describing when the snapshot was taken and from which Git
#     commit, so a restored archive can always be traced back to a version;
#   * anything else that will live in the project directory later on (the
#     SQLite database of a future phase needs no change here).
#
# Where it writes and how many archives it keeps come from the .env file of the
# installation - BACKUP_DIR and BACKUP_KEEP - so this script has no knowledge of
# any particular machine.
#
# Usage:
#   scripts/backup.sh                  # normal run, used by the systemd timer
#   scripts/backup.sh --dry-run        # show what would happen, write nothing
#   BACKUP_DIR=/tmp/x scripts/backup.sh   # override the destination once
#
# Exit codes: 0 success, 1 configuration or runtime error.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Locate the installation.  The script resolves its own path, so it works when
# called by systemd, by cron, from another directory or through a symlink.
# --------------------------------------------------------------------------- #
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname "$(dirname "${SCRIPT_PATH}")")"
PROJECT_NAME="$(basename "${PROJECT_DIR}")"
ENV_FILE="${PROJECT_DIR}/.env"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
elif [[ $# -gt 0 ]]; then
    echo "usage: $(basename "$0") [--dry-run]" >&2
    exit 1
fi

log() { printf '%s | %s\n' "$(date -Is)" "$*"; }
die() { printf '%s | ERROR: %s\n' "$(date -Is)" "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# Read one variable from .env without sourcing the file: sourcing would execute
# whatever it contains, and .env is a data file, not a script.
# --------------------------------------------------------------------------- #
read_env() {
    local key="$1" default="$2" value=""
    if [[ -f "${ENV_FILE}" ]]; then
        value="$(sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*(.*)$/\1/p" \
            "${ENV_FILE}" | tail -n 1 | sed -E 's/[[:space:]]+$//')"
        value="${value%\"}"; value="${value#\"}"   # strip double quotes
        value="${value%\'}"; value="${value#\'}"   # strip single quotes
    fi
    printf '%s' "${value:-${default}}"
}

# The real environment wins over .env, which is what makes the one-off override
# in the usage note above work.
BACKUP_DIR="${BACKUP_DIR:-$(read_env BACKUP_DIR /mnt/backup/emma)}"
BACKUP_KEEP="${BACKUP_KEEP:-$(read_env BACKUP_KEEP 14)}"

[[ "${BACKUP_KEEP}" =~ ^[0-9]+$ && "${BACKUP_KEEP}" -ge 1 ]] \
    || die "BACKUP_KEEP must be an integer >= 1, got '${BACKUP_KEEP}'"
command -v tar >/dev/null || die "tar is not installed"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="${BACKUP_DIR}/${PROJECT_NAME}-${TIMESTAMP}.tar.gz"

log "project:     ${PROJECT_DIR}"
log "destination: ${BACKUP_DIR}"
log "rotation:    keep ${BACKUP_KEEP} archives"

if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry run: would write ${ARCHIVE}"
    exit 0
fi

# --------------------------------------------------------------------------- #
# Destination.  0700 on the directory and 0600 on the archives: the tarball
# contains .env, hence the API key.
# --------------------------------------------------------------------------- #
mkdir -p "${BACKUP_DIR}" || die "cannot create ${BACKUP_DIR} (is the disk mounted?)"
chmod 700 "${BACKUP_DIR}"
[[ -w "${BACKUP_DIR}" ]] || die "${BACKUP_DIR} is not writable by $(id -un)"

# --------------------------------------------------------------------------- #
# Manifest: what this snapshot is, and where it came from.
# --------------------------------------------------------------------------- #
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

GIT_COMMIT="not a git repository"
if command -v git >/dev/null && git -C "${PROJECT_DIR}" rev-parse --git-dir >/dev/null 2>&1; then
    GIT_COMMIT="$(git -C "${PROJECT_DIR}" log -1 --pretty='%H %ci %s' 2>/dev/null || echo unknown)"
    if [[ -n "$(git -C "${PROJECT_DIR}" status --porcelain 2>/dev/null)" ]]; then
        GIT_COMMIT="${GIT_COMMIT} (working tree has uncommitted changes)"
    fi
fi

# --------------------------------------------------------------------------- #
# Database snapshot.  Copying a live SQLite file with tar can archive a
# half-written transaction: the archive reads back fine, the database inside it
# does not.  VACUUM INTO produces a consistent copy of a database that is being
# written to, which is exactly the situation here -- the service keeps running
# during the backup.  The snapshot is archived in place of data/, which is
# excluded from the tar below.
# --------------------------------------------------------------------------- #
# Relative paths are anchored to the project directory and absolute ones are
# taken as they are, matching how config.py resolves MEMORY_DB_PATH.  Getting
# this wrong would silently snapshot nothing.
MEMORY_DB="$(read_env MEMORY_DB_PATH data/emma.db)"
[[ "${MEMORY_DB}" == /* ]] || MEMORY_DB="${PROJECT_DIR}/${MEMORY_DB}"
DB_STATUS="no database file (nothing to snapshot)"

if [[ -f "${MEMORY_DB}" ]]; then
    if ! command -v sqlite3 >/dev/null; then
        DB_STATUS="NOT INCLUDED: sqlite3 is not installed (apt install sqlite3)"
        log "warning: sqlite3 missing, the conversation history is not in this archive"
    elif sqlite3 "${MEMORY_DB}" "VACUUM INTO '${STAGING}/emma.db'" 2>/dev/null; then
        # A snapshot that cannot be read back is worse than none, because it
        # would be trusted at restore time.
        if sqlite3 "${STAGING}/emma.db" "PRAGMA integrity_check" 2>/dev/null | grep -qx ok; then
            DB_STATUS="emma.db (consistent snapshot, integrity verified)"
            log "database snapshot written and verified"
        else
            rm -f "${STAGING}/emma.db"
            DB_STATUS="NOT INCLUDED: the snapshot failed its integrity check"
            log "warning: the database snapshot is corrupt and was discarded"
        fi
    else
        DB_STATUS="NOT INCLUDED: VACUUM INTO failed (is the database corrupt?)"
        log "warning: could not snapshot the database, continuing without it"
    fi
fi

cat > "${STAGING}/MANIFEST.txt" <<EOF
EMMA backup manifest
======================
created:     $(date -Is)
host:        $(hostname)
user:        $(id -un)
project dir: ${PROJECT_DIR}
git commit:  ${GIT_COMMIT}
python:      $(command -v python3 >/dev/null && python3 --version 2>&1 || echo "not found")
database:    ${DB_STATUS}

Restore:
  1. tar -xzf <this-archive>.tar.gz -C /tmp
  2. copy the restored directory over the installation, or to a new one
  3. put the conversation history back, if this archive carries one:
       mkdir -p <install>/data && cp emma.db <install>/data/emma.db
     (the snapshot sits beside this manifest, not under data/)
  4. check .env, recreate the virtual environment, restart the service
  The full procedure is in chapter 6 of docs/GUIDA.pdf.
EOF

# --------------------------------------------------------------------------- #
# Archive.  The excluded paths are all reproducible from the repository plus
# requirements.txt: there is no point paying disk for them every single day.
# .cache in particular is pip's HTTP cache, which lands here because the
# installation directory doubles as the home of the emma user; left in, it was
# by far the largest thing in every archive.
# data/ is excluded for a different reason -- it holds the live database, which
# is archived above as a consistent snapshot instead.
# --------------------------------------------------------------------------- #
log "creating ${ARCHIVE}"
umask 077

STAGED_FILES=(MANIFEST.txt)
[[ -f "${STAGING}/emma.db" ]] && STAGED_FILES+=(emma.db)

tar --create --gzip --file "${ARCHIVE}" \
    --exclude="${PROJECT_NAME}/.venv" \
    --exclude="${PROJECT_NAME}/venv" \
    --exclude="${PROJECT_NAME}/data" \
    --exclude="${PROJECT_NAME}/.cache" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude="${PROJECT_NAME}/.git/objects/pack/tmp_*" \
    -C "$(dirname "${PROJECT_DIR}")" "${PROJECT_NAME}" \
    -C "${STAGING}" "${STAGED_FILES[@]}"
chmod 600 "${ARCHIVE}"

# A backup that cannot be read is not a backup: verify before rotating, so a
# failed run never causes a good older archive to be deleted.
tar -tzf "${ARCHIVE}" >/dev/null 2>&1 \
    || die "the archive just written is unreadable: ${ARCHIVE}"

log "written $(du -h "${ARCHIVE}" | cut -f1) to ${ARCHIVE}"

# --------------------------------------------------------------------------- #
# Rotation: keep the newest BACKUP_KEEP archives, delete the rest.  Names are
# sorted lexicographically, which for this timestamp format is chronological.
# --------------------------------------------------------------------------- #
mapfile -t ARCHIVES < <(find "${BACKUP_DIR}" -maxdepth 1 -type f \
    -name "${PROJECT_NAME}-*.tar.gz" -printf '%f\n' | sort -r)

if [[ "${#ARCHIVES[@]}" -gt "${BACKUP_KEEP}" ]]; then
    for stale in "${ARCHIVES[@]:${BACKUP_KEEP}}"; do
        log "rotating out ${stale}"
        rm -f -- "${BACKUP_DIR}/${stale}"
    done
fi

log "done: ${#ARCHIVES[@]} archive(s) before rotation, $(find "${BACKUP_DIR}" -maxdepth 1 \
    -type f -name "${PROJECT_NAME}-*.tar.gz" | wc -l) kept"
