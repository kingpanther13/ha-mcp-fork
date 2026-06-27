#!/usr/bin/env bash
# apply-fork-overrides.sh — refresh addon-repo with master + a PR, apply
# Fork-Dev and NabuForkDev overrides, run verification grep, commit, push.
#
# Usage:
#   ./fork-dev-scripts/apply-fork-overrides.sh --pr <N> [--pr <N> ...] --bump fork-dev|nabu|both [opts]
#
# Examples:
#   ./fork-dev-scripts/apply-fork-overrides.sh --pr 1184 --bump nabu --version dev4
#   ./fork-dev-scripts/apply-fork-overrides.sh --pr 1126 --bump fork-dev          # auto-increments
#   ./fork-dev-scripts/apply-fork-overrides.sh --pr 1168 --pr 1184 --bump both    # both PRs, both versions bumped
#
# Options:
#   --pr <N>            PR number to merge on top of upstream/master    [required, repeatable]
#   --bump fork-dev|nabu|both  Which addon(s) to bump this cycle        [required]
#   --version dev<N>    Explicit new version for the (single) bumped addon
#                       (ignored when --bump both — auto-increments both)
#   --no-commit         Stop after applying overrides
#   --no-push           Stop after commit
#   -h, --help          This help
#
# Fully self-contained. Lives on `addon-repo` branch under fork-dev-scripts/
# alongside FORK-DEV.md (the doc this script restores after each reset).
# Clone the fork, checkout addon-repo, and run from any machine — no
# external backup files required.

set -euo pipefail

# ---------------------------------------------------------------------------
# Self-protect: re-exec from a temp copy so the script body survives being
# wiped during `git reset --hard upstream/master`. fork-dev-scripts/ does not
# exist on upstream/master, so without this the script would unlink itself
# mid-run.
# ---------------------------------------------------------------------------
if [[ "${HAMCP_FORK_OVERRIDES_REEXEC:-}" != "1" ]]; then
    HAMCP_ORIG_SCRIPT="$(readlink -f -- "$0")"
    export HAMCP_ORIG_SCRIPT
    TMP_SELF="$(mktemp "${TMPDIR:-/tmp}/apply-fork-overrides.XXXXXX.sh")"
    cp -- "$0" "$TMP_SELF"
    chmod +x "$TMP_SELF"
    export HAMCP_FORK_OVERRIDES_REEXEC=1
    exec bash "$TMP_SELF" "$@"
fi

# ---------------------------------------------------------------------------
# Locate repo from the original script's path. Works for both
# <repo>/fork-dev-scripts/apply-fork-overrides.sh (canonical) and
# <repo>/local/apply-fork-overrides.sh (legacy gitignored copy).
# ---------------------------------------------------------------------------
SCRIPT_PARENT_DIR="$(dirname -- "$HAMCP_ORIG_SCRIPT")"
REPO="$(cd "$SCRIPT_PARENT_DIR/.." && git rev-parse --show-toplevel 2>/dev/null)" \
    || { echo "Could not locate git repo from $HAMCP_ORIG_SCRIPT" >&2; exit 1; }

FORK_URL="https://github.com/kingpanther13/ha-mcp-fork"
PROTECTED_DIR="fork-dev-scripts"
BACKUP_DOC="$PROTECTED_DIR/FORK-DEV.md"  # in-tree, lives inside PROTECTED_DIR

PRS=()
BUMP=""
NEW_VERSION=""
NO_COMMIT=0
NO_PUSH=0

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \?//' ; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pr) PRS+=("$2"); shift 2 ;;
        --bump) BUMP="$2"; shift 2 ;;
        --version) NEW_VERSION="$2"; shift 2 ;;
        --no-commit) NO_COMMIT=1; shift ;;
        --no-push) NO_PUSH=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown arg: $1" >&2; usage 1 ;;
    esac
done

[[ ${#PRS[@]} -gt 0 && -n "$BUMP" ]] || { echo "Missing --pr or --bump" >&2; usage 1; }
[[ "$BUMP" == "fork-dev" || "$BUMP" == "nabu" || "$BUMP" == "both" ]] || { echo "--bump must be fork-dev, nabu, or both" >&2; exit 1; }
[[ "$BUMP" == "both" && -n "$NEW_VERSION" ]] && { echo "--version is incompatible with --bump both (use auto-increment)" >&2; exit 1; }

cd "$REPO"

[[ -f "$BACKUP_DOC" ]] || { echo "Missing $BACKUP_DOC in repo. Are you on the addon-repo branch?" >&2; exit 1; }

echo "==> Fetching upstream + PR(s) ${PRS[*]}"
git fetch upstream --quiet
git checkout addon-repo --quiet

# Read current versions BEFORE reset (used for hold + auto-increment).
read_version() { awk -F'"' '/^version:/ {print $2; exit}' "$1"; }
bump_dev() {
    local v="$1"
    [[ "$v" =~ ^dev([0-9]+)$ ]] || { echo "Cannot auto-increment non-dev version: $v" >&2; exit 1; }
    echo "dev$((BASH_REMATCH[1] + 1))"
}

CUR_FORK_DEV=$(read_version homeassistant-addon-dev/config.yaml)
CUR_NABU=$(read_version homeassistant-addon-webhook-proxy/config.yaml)

case "$BUMP" in
    fork-dev) FD_VERSION="${NEW_VERSION:-$(bump_dev "$CUR_FORK_DEV")}"; NABU_VERSION="$CUR_NABU" ;;
    nabu)     FD_VERSION="$CUR_FORK_DEV"; NABU_VERSION="${NEW_VERSION:-$(bump_dev "$CUR_NABU")}" ;;
    both)     FD_VERSION=$(bump_dev "$CUR_FORK_DEV"); NABU_VERSION=$(bump_dev "$CUR_NABU") ;;
esac

echo "==> Versions:  Fork-Dev=$FD_VERSION  NabuForkDev=$NABU_VERSION  (bump target: $BUMP)"

# ---------------------------------------------------------------------------
# Stash protected dir before reset (the reset would otherwise wipe it,
# since fork-dev-scripts/ doesn't exist on upstream/master).
# ---------------------------------------------------------------------------
PROTECTED_BACKUP="$(mktemp -d)"
trap 'rm -rf -- "$PROTECTED_BACKUP" 2>/dev/null || true' EXIT
cp -a "$PROTECTED_DIR" "$PROTECTED_BACKUP/"

echo "==> Resetting addon-repo to upstream/master + merging PR(s) ${PRS[*]}"
git reset --hard upstream/master --quiet
# Fetch each PR via FETCH_HEAD (no named branch) so the script never clashes
# with worktrees that may already check out pr-<N> for review work.
for pr in "${PRS[@]}"; do
    git fetch upstream "pull/$pr/head" --quiet
    git merge FETCH_HEAD --no-ff -m "Merge branch 'pr-$pr' into addon-repo" --quiet
done

# ---------------------------------------------------------------------------
# Restore PROTECTED_DIR immediately after reset so subsequent steps can
# read FORK-DEV.md (and any other tooling we add later) from it.
# ---------------------------------------------------------------------------
echo "==> Restoring $PROTECTED_DIR/"
rm -rf "$PROTECTED_DIR"
cp -a "$PROTECTED_BACKUP/$PROTECTED_DIR" "$PROTECTED_DIR"

# ---------------------------------------------------------------------------
# Fork-Dev restore
# ---------------------------------------------------------------------------
echo "==> Restoring FORK-DEV.md, README banner, Fork-Dev addon-dev/"
cp "$BACKUP_DOC" FORK-DEV.md

if ! head -3 README.md | grep -q "personal fork"; then
    sed -i '1i> **This is a personal fork.** See [`FORK-DEV.md`](FORK-DEV.md) for the addon-repo workflow.\n' README.md
fi

cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/
rm -rf homeassistant-addon-dev/src/ha_mcp
cp -r src/ha_mcp homeassistant-addon-dev/src/ha_mcp
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# Fork-Dev config: rename, version, url, drop image:
sed -i \
    -e 's|^name: ".*"$|name: "Fork-Dev"|' \
    -e "s|^version: \".*\"\$|version: \"$FD_VERSION\"|" \
    -e "s|^url: \".*\"\$|url: \"$FORK_URL\"|" \
    -e '/^image: "ghcr\.io\/homeassistant-ai\/ha-mcp-addon-dev/d' \
    homeassistant-addon-dev/config.yaml

# ---------------------------------------------------------------------------
# NabuForkDev coexistence renames
# ---------------------------------------------------------------------------
echo "==> Applying NabuForkDev coexistence renames"

cd homeassistant-addon-webhook-proxy

# config.yaml: rename, slug, version, url
sed -i \
    -e 's|^name: ".*"$|name: "NabuForkDev"|' \
    -e "s|^version: \".*\"\$|version: \"$NABU_VERSION\"|" \
    -e 's|^slug: ".*"$|slug: "ha_mcp_webhook_proxy_dev"|' \
    -e "s|^url: \".*\"\$|url: \"$FORK_URL\"|" \
    config.yaml

# Rename integration directory (HA loads custom_components by dir name == domain).
git mv mcp_proxy mcp_proxy_dev

# Dockerfile path
sed -i 's|COPY mcp_proxy /opt/mcp_proxy|COPY mcp_proxy_dev /opt/mcp_proxy_dev|' Dockerfile

# start.py: paths, all notification IDs, domain checks, handler
sed -i \
    -e 's|/opt/mcp_proxy|/opt/mcp_proxy_dev|g' \
    -e 's|/config/custom_components/mcp_proxy|/config/custom_components/mcp_proxy_dev|g' \
    -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
    -e 's|/config/\.mcp_proxy_oauth_secret|/config/.mcp_proxy_dev_oauth_secret|g' \
    -e 's|/config/\.mcp_proxy_oauth_restart_required|/config/.mcp_proxy_dev_oauth_restart_required|g' \
    -e 's|"mcp_proxy_restart"|"mcp_proxy_dev_restart"|g' \
    -e 's|"mcp_proxy_update"|"mcp_proxy_dev_update"|g' \
    -e 's|"mcp_proxy_regen_stuck"|"mcp_proxy_dev_regen_stuck"|g' \
    -e 's|domain") == "mcp_proxy"|domain") == "mcp_proxy_dev"|g' \
    -e 's|"handler": "mcp_proxy"|"handler": "mcp_proxy_dev"|g' \
    start.py

# Integration python: loop over every .py in mcp_proxy_dev/ so newly-added
# files (oauth.py from PR #1184, repairs.py from the oauth fix branch, …)
# pick up the universal path/identifier renames without script edits.
# File-specific patterns (DOMAIN, OAUTH_BASE, h1) simply don't match in
# files that don't contain them — sed is happy with that.
for f in mcp_proxy_dev/*.py; do
    sed -i \
        -e 's|DOMAIN = "mcp_proxy"|DOMAIN = "mcp_proxy_dev"|g' \
        -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
        -e 's|/config/\.mcp_proxy_oauth_secret|/config/.mcp_proxy_dev_oauth_secret|g' \
        -e 's|/config/\.mcp_proxy_oauth_restart_required|/config/.mcp_proxy_dev_oauth_restart_required|g' \
        -e 's|OAUTH_BASE = "/api/mcp_proxy/oauth"|OAUTH_BASE = "/api/mcp_proxy_dev/oauth"|g' \
        -e 's|<h1>Authorize MCP Webhook Proxy</h1>|<h1>Authorize MCP Webhook Proxy (NabuForkDev)</h1>|g' \
        "$f"
done

# manifest.json: domain, name, and version (suffixed with NABU_VERSION so
# start.py:_install_integration sees a change and redeploys the integration
# whenever NabuForkDev bumps).
MANIFEST_BASE=$(awk -F'"' '/"version":/ {print $4; exit}' mcp_proxy_dev/manifest.json)
MANIFEST_BASE="${MANIFEST_BASE%%+*}"  # strip any existing +devN suffix
MANIFEST_VERSION="${MANIFEST_BASE}+${NABU_VERSION}"
sed -i \
    -e 's|"domain": "mcp_proxy",|"domain": "mcp_proxy_dev",|' \
    -e 's|"name": "MCP Webhook Proxy",|"name": "MCP Webhook Proxy (NabuForkDev)",|' \
    -e "s|\"version\": \".*\"|\"version\": \"$MANIFEST_VERSION\"|" \
    mcp_proxy_dev/manifest.json

sed -i \
    -e 's|"title": "MCP Webhook Proxy",|"title": "MCP Webhook Proxy (NabuForkDev)",|' \
    -e 's|"already_configured": "MCP Webhook Proxy is already configured."|"already_configured": "MCP Webhook Proxy (NabuForkDev) is already configured."|' \
    mcp_proxy_dev/strings.json

# translations/*.yaml: rename any quoted "mcp_proxy" domain references (e.g. log filter hints)
for f in translations/*.yaml; do
    [[ -f "$f" ]] && sed -i -e 's|"mcp_proxy"|"mcp_proxy_dev"|g' "$f"
done

sed -i 's|title="MCP Webhook Proxy"|title="MCP Webhook Proxy (NabuForkDev)"|g' \
    mcp_proxy_dev/config_flow.py

# DOCS.md
sed -i \
    -e 's|/api/mcp_proxy/oauth|/api/mcp_proxy_dev/oauth|g' \
    -e 's|/config/\.mcp_proxy_oauth_secret|/config/.mcp_proxy_dev_oauth_secret|g' \
    -e 's|/config/\.mcp_proxy_oauth_restart_required|/config/.mcp_proxy_dev_oauth_restart_required|g' \
    -e 's|/config/custom_components/mcp_proxy/|/config/custom_components/mcp_proxy_dev/|g' \
    -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
    DOCS.md

cd "$REPO"

# ---------------------------------------------------------------------------
# Verification grep — fail if any rename was missed
# ---------------------------------------------------------------------------
echo "==> Running verification grep"
HITS=$(grep -rnE 'mcp_proxy"|ha_mcp_webhook_proxy"|/config/custom_components/mcp_proxy/|/config/\.mcp_proxy_config\.json|/config/\.mcp_proxy_oauth_secret|/config/\.mcp_proxy_oauth_restart_required|/api/mcp_proxy/oauth|/opt/mcp_proxy"' homeassistant-addon-webhook-proxy/ 2>/dev/null | grep -v __pycache__ || true)
if [[ -n "$HITS" ]]; then
    echo "❌ Verification failed — missed renames:" >&2
    echo "$HITS" >&2
    echo "" >&2
    echo "Add new sed patterns to apply-fork-overrides.sh and re-run." >&2
    exit 1
fi
echo "    verification clean (no missed identifiers)"

# ---------------------------------------------------------------------------
# Sync the latest in-tree FORK-DEV.md back into fork-dev-scripts/ so any
# edits the maintainer made between cycles travel with the branch on the
# next push. (FORK-DEV.md was just rewritten from $BACKUP_DOC above; this
# captures the case where the maintainer edited the live FORK-DEV.md in
# the working tree before re-running the script — without this, the edit
# would be reverted on the next cycle.)
# ---------------------------------------------------------------------------
if ! diff -q FORK-DEV.md "$BACKUP_DOC" >/dev/null 2>&1; then
    cp FORK-DEV.md "$BACKUP_DOC"
fi

# ---------------------------------------------------------------------------
# Commit + push
# ---------------------------------------------------------------------------
[[ "$NO_COMMIT" == 1 ]] && { echo "==> --no-commit, stopping"; exit 0; }

echo "==> Committing"
git add -A
PR_LIST=$(printf 'PR #%s, ' "${PRS[@]}"); PR_LIST="${PR_LIST%, }"
case "$BUMP" in
    fork-dev) COMMIT_TARGET="Fork-Dev $FD_VERSION" ;;
    nabu)     COMMIT_TARGET="NabuForkDev $NABU_VERSION" ;;
    both)     COMMIT_TARGET="Fork-Dev $FD_VERSION + NabuForkDev $NABU_VERSION" ;;
esac
git commit -q -m "$(cat <<EOF
chore: reset addon-repo to upstream master + $PR_LIST, $COMMIT_TARGET

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

[[ "$NO_PUSH" == 1 ]] && { echo "==> --no-push, stopping"; exit 0; }

echo "==> Force-pushing addon-repo"
git push origin addon-repo --force --quiet
HEAD_SHA=$(git rev-parse --short addon-repo)
echo "    pushed $HEAD_SHA"
echo "==> Done. Fork-Dev=$FD_VERSION  NabuForkDev=$NABU_VERSION"
