#!/usr/bin/env bash
#
# deploy.sh - put the working tree on the server, and record what was put there.
#
# Run from the development machine, from the project root:
#
#   scripts/deploy.sh                 # deploy, after the usual checks
#   scripts/deploy.sh --dry-run       # show what would happen, change nothing
#
# Every deploy before this one was a sequence of commands typed by hand: build
# a tar, copy it, extract, fix permissions, restart. It worked, and it left no
# trace -- one deploy on 31 August could not afterwards be tied to the command
# that made it. A script is the fix for both halves of that: it does the same
# thing every time, and it writes down what it did.
#
# What it writes is the VERSION stamp. The server has no git checkout -- code
# arrives as an archive -- so nothing there can tell you which commit is
# running unless the deploy records it. That is why the stamp is written here
# and not by hand: a step that must be remembered is a step that will be
# forgotten, and an assistant reporting a stale version does it with total
# confidence.
#
# Configuration, through the environment:
#
#   EMMA_DEPLOY_HOST   ssh destination            (default: emma-deploy)
#   EMMA_REMOTE_DIR    installation directory     (default: /opt/emma)
#   EMMA_SERVICE       systemd unit               (default: emma.service)
#   EMMA_KEEP_DAYS     days of safety copies kept (default: 3)
#
# EMMA_DEPLOY_HOST is a name, not an address: put the real destination in
# ~/.ssh/config, which is not in this repository. It needs the administrative
# key, not the restricted queue key -- deploying is exactly what that key is
# kept separate for.
#
# Exit codes: 0 success, 1 refused or failed. It refuses rather than proceeds
# whenever the state is not one you would want deployed.

set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname "$(dirname "${SCRIPT_PATH}")")"

HOST="${EMMA_DEPLOY_HOST:-emma-deploy}"
REMOTE="${EMMA_REMOTE_DIR:-/opt/emma}"
SERVICE="${EMMA_SERVICE:-emma.service}"
KEEP_DAYS="${EMMA_KEEP_DAYS:-3}"

[[ "${KEEP_DAYS}" =~ ^[0-9]+$ ]] || { echo "EMMA_KEEP_DAYS must be a number" >&2; exit 1; }

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
elif [[ $# -gt 0 ]]; then
    echo "usage: $(basename "$0") [--dry-run]" >&2
    exit 1
fi

log() { printf '%s | %s\n' "$(date -Is)" "$*"; }
die() { printf '%s | ERROR: %s\n' "$(date -Is)" "$*" >&2; exit 1; }

cd "${PROJECT_DIR}"

# --------------------------------------------------------------------------- #
# What is about to be deployed.
#
# The commit is what the stamp is for, so a tree with uncommitted changes would
# make it a lie: the server would report a commit that does not describe the
# code running on it. Refuse instead of stamping something untrue.
# --------------------------------------------------------------------------- #
command -v git >/dev/null || die "git is not installed"
git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository"

COMMIT="$(git rev-parse --short HEAD)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' core/version.py | head -1)"
[[ -n "${VERSION}" ]] || die "cannot read VERSION from core/version.py"

if [[ -n "$(git status --porcelain)" ]]; then
    git status --short | sed 's/^/    /' >&2
    die "the working tree has uncommitted changes; the stamp would not describe what runs"
fi

BUILT="$(date -Is)"

log "project:  ${PROJECT_DIR}"
log "version:  ${VERSION}"
log "commit:   ${COMMIT}"
log "target:   ${HOST}:${REMOTE}"

# --------------------------------------------------------------------------- #
# Verify before shipping. A deploy is not the moment to find out.
# --------------------------------------------------------------------------- #
PYTHON=".venv/Scripts/python.exe"
[[ -x "${PYTHON}" ]] || PYTHON=".venv/bin/python"
if [[ -x "${PYTHON}" ]]; then
    log "running the test suite"
    "${PYTHON}" -m pytest -q >/dev/null || die "tests fail; nothing was deployed"
    "${PYTHON}" -m ruff check . >/dev/null || die "ruff reports problems; nothing was deployed"
else
    log "warning: no virtual environment found, skipping the local checks"
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry run: would deploy ${COMMIT} to ${HOST}:${REMOTE} and restart ${SERVICE}"
    exit 0
fi

# --------------------------------------------------------------------------- #
# The archive. .env and data/ are excluded and never travel: the first is the
# server's own secret, the second is the conversation history. Overwriting
# either from a development machine would be a data loss with no undo.
# --------------------------------------------------------------------------- #
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT
ARCHIVE="${STAGING}/emma.tar.gz"

log "building the archive"
tar --create --gzip --file "${ARCHIVE}" \
    --exclude='.env' \
    --exclude='data' \
    --exclude='.venv' \
    --exclude='.git' \
    --exclude='.cache' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='uv.lock' \
    --exclude='VERSION' \
    .

# Belt and braces: an archive carrying either of those would be caught here
# rather than on the server, after it had already overwritten something.
if tar -tzf "${ARCHIVE}" | grep -qE '^\./\.env$|^\./data/'; then
    die "the archive contains .env or data/; refusing to deploy"
fi

log "copying to ${HOST}"
scp -q "${ARCHIVE}" "${HOST}:/tmp/emma-deploy.tar.gz"

# --------------------------------------------------------------------------- #
# The remote half. Written as one script so a broken connection cannot leave
# the service stopped halfway through.
# --------------------------------------------------------------------------- #
log "deploying"
ssh "${HOST}" "REMOTE='${REMOTE}' SERVICE='${SERVICE}' VERSION='${VERSION}' COMMIT='${COMMIT}' BUILT='${BUILT}' KEEP_DAYS='${KEEP_DAYS}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

stamp_time="$(date +%Y%m%d-%H%M%S)"
safe="/root/emma-pre-deploy-${stamp_time}"
mkdir -p "${safe}"
cp -a "${REMOTE}/.env" "${REMOTE}/data" "${safe}/" 2>/dev/null || true
chmod -R 700 "${safe}"
echo "  safety copy: ${safe}"

systemctl stop "${SERVICE}"

tar -xzf /tmp/emma-deploy.tar.gz -C "${REMOTE}"

# The stamp. Written after extraction, because the archive deliberately does
# not carry one: it describes this deployment, not the source it came from.
cat > "${REMOTE}/VERSION" <<STAMP
version=${VERSION}
commit=${COMMIT}
built=${BUILT}
STAMP

chown -R emma:emma "${REMOTE}"
chmod 750 "${REMOTE}"
chmod 600 "${REMOTE}/.env" "${REMOTE}/VERSION"
chmod 700 "${REMOTE}/data"
chmod 750 "${REMOTE}"/scripts/*.sh
[ -f "${REMOTE}/.ssh/authorized_keys" ] && chmod 600 "${REMOTE}/.ssh/authorized_keys"

cd "${REMOTE}"
sudo -u emma "${REMOTE}/.venv/bin/python" -m pytest -q >/dev/null \
    || { echo "  ERROR: the test suite fails on the server" >&2; exit 1; }

systemctl start "${SERVICE}"
sleep 6
systemctl is-active --quiet "${SERVICE}" || { echo "  ERROR: the service did not come back" >&2; exit 1; }

echo "  service: active"
echo "  stamped: $(tr '\n' ' ' < "${REMOTE}/VERSION")"

# Prune old safety copies. Deliberately after the service is confirmed back up:
# if anything above failed, every copy is still there to fall back on.
#
# The pattern covers the ones left by hand-written deploys as well, since those
# are exactly the ones nobody will remember to remove. Today's is never touched:
# -mtime +N means strictly older than N days.
pruned="$(find /root -maxdepth 1 -type d -name 'emma-pre-*' -mtime "+${KEEP_DAYS}" -print -exec rm -rf {} + 2>/dev/null | wc -l)"
kept="$(find /root -maxdepth 1 -type d -name 'emma-pre-*' | wc -l)"
echo "  safety copies: ${kept} kept, ${pruned} pruned (older than ${KEEP_DAYS} days)"

rm -f /tmp/emma-deploy.tar.gz
REMOTE_SCRIPT

log "deployed ${VERSION} (${COMMIT})"
