# Fork Development Workflow

This branch (`addon-repo`) is the **default branch** on this fork. It serves as the HA add-on repository for testing PR branches on a real Home Assistant instance.

**PRs are NOT based on this branch.** Feature branches go to `upstream/master` independently. This branch is always force-pushed to mirror whatever feature branch is being tested.

> **A backup of this file lives at `~/.ha-mcp-fork-dev.md`.**
> If `git reset --hard` wipes it, restore with: `cp ~/.ha-mcp-fork-dev.md ~/ha-mcp-fork/FORK-DEV.md`

## TL;DR — Use the Script

The full restore + rename workflow is automated in `local/apply-fork-overrides.sh` (gitignored, survives `git reset --hard`; backup at `~/.ha-mcp-fork-overrides.sh`).

```bash
# Bump Fork-Dev (default cycle):
./local/apply-fork-overrides.sh --pr 1126 --bump fork-dev

# Bump NabuForkDev (when explicitly requested):
./local/apply-fork-overrides.sh --pr 1184 --bump nabu --version dev4

# Smoke-test without pushing:
./local/apply-fork-overrides.sh --pr 1184 --bump nabu --no-push
```

Without `--version`, the script auto-increments the chosen addon's `dev<N>`. It runs the verification grep at the end and exits non-zero if any rename was missed — so when upstream adds a new identifier (like `oauth.py` arriving via PR #1184), the failure is loud and you add a new sed pattern to the script.

The rest of this document describes what the script does, for cases where you need to debug or run a step manually.

## Two Addons Live Here

This repository ships **two** HA add-ons — both must be configured correctly each cycle:

| Addon | Directory | Renamed To | Purpose |
|-------|-----------|------------|---------|
| **Fork-Dev** | `homeassistant-addon-dev/` | `Fork-Dev` | Main MCP server (the thing under PR test) |
| **NabuForkDev** | `homeassistant-addon-webhook-proxy/` | `NabuForkDev` | Remote-access webhook proxy |

Both addon configs are restored to their upstream defaults by `git reset --hard`, so each cycle re-applies the rename + version + url + (Fork-Dev only) `image:` removal.

## Version Bumping Rule

**Every addon-repo update bumps the dev version of exactly one addon — never both at once.**

- **Default:** bump Fork-Dev (`homeassistant-addon-dev/config.yaml`). NabuForkDev's version stays put.
- **When user explicitly asks** (e.g., "bump nabu fork dev too" / "push NabuForkDev to dev1"): bump NabuForkDev instead. Fork-Dev's version stays put.

Versions follow `dev<N>` (currently Fork-Dev around dev100, NabuForkDev at dev0). HA Supervisor only rebuilds the addon whose version changed, so leaving the other one alone avoids spurious rebuilds.

## How It Works

1. HA Supervisor clones this repo's default branch (`addon-repo`)
2. It scans the repo for addon directories, finds both `homeassistant-addon-dev/config.yaml` (Fork-Dev) and `homeassistant-addon-webhook-proxy/config.yaml` (NabuForkDev)
3. For Fork-Dev, it builds the Docker image from `homeassistant-addon-dev/Dockerfile` and runs the code from `homeassistant-addon-dev/src/ha_mcp/`
4. For NabuForkDev, it builds from `homeassistant-addon-webhook-proxy/Dockerfile` (no `image:` field upstream — already builds locally)

## Dual `src/` Directories - READ THIS

The repo has **two separate `src/ha_mcp/` directories**:

```
ha-mcp-fork/
  src/ha_mcp/                          <-- repo root source (what PRs modify)
  homeassistant-addon-dev/src/ha_mcp/  <-- addon source (what HA actually runs)
```

**The Dockerfile copies from `homeassistant-addon-dev/src/`, NOT from the root `src/`.** If you only edit files in the root `src/`, the addon will still run the OLD code. You must always sync changes into `homeassistant-addon-dev/src/ha_mcp/`.

This is the #1 cause of "I pushed but the old code is still running" issues.

## Switching to a Different PR Branch

```bash
git -C ~/ha-mcp-fork checkout addon-repo
git -C ~/ha-mcp-fork fetch upstream
git -C ~/ha-mcp-fork fetch upstream pull/<PR>/head:pr-<PR> --force

# Reset to upstream/master, then merge the PR on top
git -C ~/ha-mcp-fork reset --hard upstream/master
git -C ~/ha-mcp-fork merge pr-<PR> --no-ff -m "Merge branch 'pr-<PR>' into addon-repo"
```

### After `git reset --hard` - CRITICAL STEPS

The reset wipes addon-specific files that don't exist on feature branches. You **must** restore them:

```bash
# 1. Restore this documentation (gets wiped by reset!)
cp ~/.ha-mcp-fork-dev.md FORK-DEV.md

# 2. Restore the README banner
#    Add at the very top of README.md:
#    > **This is a personal fork.** See [`FORK-DEV.md`](FORK-DEV.md) for the addon-repo workflow.

# 3. Copy build files into the Fork-Dev addon directory
cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/

# 4. Copy source code (the critical sync step!)
rm -rf homeassistant-addon-dev/src/ha_mcp
cp -r src/ha_mcp homeassistant-addon-dev/src/ha_mcp

# 5. Fix the Dockerfile (upstream references wrong path)
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# 6. Fork-Dev config.yaml — homeassistant-addon-dev/config.yaml:
#    - Remove the `image:` line (forces local build instead of pulling from ghcr.io)
#    - name: "Fork-Dev"
#    - url: "https://github.com/kingpanther13/ha-mcp-fork"
#    - version: bump dev<N> → dev<N+1>  (UNLESS this cycle is bumping NabuForkDev instead)

# 7. NabuForkDev config.yaml — homeassistant-addon-webhook-proxy/config.yaml:
#    - name: "NabuForkDev"
#    - url: "https://github.com/kingpanther13/ha-mcp-fork"
#    - slug: "ha_mcp_webhook_proxy"  →  "ha_mcp_webhook_proxy_dev"
#    - version: keep current dev<N> unchanged  (UNLESS user asked to bump this one)
#    - No `image:` field upstream, so nothing to remove

# 8. NabuForkDev custom-component renames — REQUIRED so it can coexist with the
#    prod webhook-proxy on the same HA instance (different slug, different
#    custom_component domain, different file paths). Apply EVERY cycle, since
#    git reset --hard wipes them too:
cd ~/ha-mcp-fork/homeassistant-addon-webhook-proxy

# 8a. Rename the integration directory (HA loads custom_components by dir name == domain)
git mv mcp_proxy mcp_proxy_dev

# 8b. Edit Dockerfile: COPY mcp_proxy /opt/mcp_proxy → COPY mcp_proxy_dev /opt/mcp_proxy_dev

# 8c. Bulk-rewrite identifiers in start.py — covers paths, ALL notification IDs
#     (mcp_proxy_restart, mcp_proxy_update, mcp_proxy_regen_stuck, …),
#     domain checks and config-flow handler:
sed -i \
  -e 's|/opt/mcp_proxy|/opt/mcp_proxy_dev|g' \
  -e 's|/config/custom_components/mcp_proxy|/config/custom_components/mcp_proxy_dev|g' \
  -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
  -e 's|"mcp_proxy_restart"|"mcp_proxy_dev_restart"|g' \
  -e 's|"mcp_proxy_update"|"mcp_proxy_dev_update"|g' \
  -e 's|"mcp_proxy_regen_stuck"|"mcp_proxy_dev_regen_stuck"|g' \
  -e 's|domain") == "mcp_proxy"|domain") == "mcp_proxy_dev"|g' \
  -e 's|"handler": "mcp_proxy"|"handler": "mcp_proxy_dev"|g' \
  start.py
# After running, verify with:  grep -nE '"mcp_proxy[^"]*"' start.py
# Every match must end in `_dev` or `_dev_…`.

# 8d. Rewrite identifiers in the renamed integration (covers oauth.py from
#     PR #1184 — OAUTH_BASE and SECRET_FILE):
sed -i \
  -e 's|DOMAIN = "mcp_proxy"|DOMAIN = "mcp_proxy_dev"|g' \
  -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
  -e 's|OAUTH_BASE = "/api/mcp_proxy/oauth"|OAUTH_BASE = "/api/mcp_proxy_dev/oauth"|g' \
  -e 's|/config/\.mcp_proxy_oauth_secret|/config/.mcp_proxy_dev_oauth_secret|g' \
  mcp_proxy_dev/__init__.py mcp_proxy_dev/config_flow.py mcp_proxy_dev/oauth.py

# 8e. Edit mcp_proxy_dev/manifest.json:
#    - "domain": "mcp_proxy"  →  "mcp_proxy_dev"
#    - "name": "MCP Webhook Proxy"  →  "MCP Webhook Proxy (NabuForkDev)"

# 8f. Edit mcp_proxy_dev/strings.json + mcp_proxy_dev/config_flow.py +
#     mcp_proxy_dev/oauth.py (consent-page <h1>):
#    - all "MCP Webhook Proxy" user-visible titles → "MCP Webhook Proxy (NabuForkDev)"

# 8g. Patch DOCS.md to match — replace `/api/mcp_proxy/oauth`,
#     `/config/.mcp_proxy_oauth_secret`, `/config/custom_components/mcp_proxy/`,
#     and `/config/.mcp_proxy_config.json` with their `_dev` equivalents.

# 8h. Final verification — every command below must print nothing:
grep -rnE 'mcp_proxy"|ha_mcp_webhook_proxy"|/config/custom_components/mcp_proxy/|/config/\.mcp_proxy_config\.json|/config/\.mcp_proxy_oauth_secret|/api/mcp_proxy/oauth|/opt/mcp_proxy"' . | grep -v __pycache__
```

### Why These Files Are Needed

| File | Why |
|------|-----|
| `homeassistant-addon-dev/pyproject.toml` | Dockerfile `COPY pyproject.toml` - needed for `uv sync` |
| `homeassistant-addon-dev/uv.lock` | Dockerfile `COPY uv.lock` - pinned dependencies |
| `homeassistant-addon-dev/start.py` | Dockerfile `COPY start.py /` - addon entrypoint |
| `homeassistant-addon-dev/src/ha_mcp/` | Dockerfile `COPY src/` - the actual server code |
| `homeassistant-addon-dev/Dockerfile` | Must use `COPY start.py /` not `COPY homeassistant-addon/start.py /` |
| `homeassistant-addon-webhook-proxy/mcp_proxy_dev/` | Integration directory renamed from `mcp_proxy/` so HA registers it under domain `mcp_proxy_dev` (HA loads custom_components by dir name == domain). Without this, NabuForkDev collides with the prod webhook-proxy. |
| `homeassistant-addon-webhook-proxy/Dockerfile` | `COPY mcp_proxy_dev /opt/mcp_proxy_dev` (matches renamed dir) |
| `homeassistant-addon-webhook-proxy/config.yaml` | `slug: ha_mcp_webhook_proxy_dev` so HA Supervisor treats it as a separate addon from the prod webhook-proxy |
| `FORK-DEV.md` | This file - backup at `~/.ha-mcp-fork-dev.md` |

The upstream Dockerfile is designed for CI builds where the build context is the repo root. When HA Supervisor builds locally, the build context is `homeassistant-addon-dev/` itself, so all paths must be relative to that directory.

### Fork-Dev config.yaml: `image` Field

- **With `image:` field**: HA pulls a pre-built image from ghcr.io (upstream code, NOT your branch)
- **Without `image:` field**: HA builds locally from the Dockerfile (your branch code)
- For testing fork branches, the `image:` field **must be removed**

NabuForkDev's upstream config has no `image:` field — nothing to remove there.

## Forcing a Rebuild

HA Supervisor only rebuilds the addon whose version changed. To trigger a rebuild of one addon:

- Bump that addon's `version:` in its `config.yaml` (default cycle: Fork-Dev only).
- Force-push to `addon-repo` (safe — this branch is never a PR base).
- HA Supervisor will detect the version change and offer a rebuild for that specific addon.

## Full Deploy Workflow (Copy-Paste)

```bash
# Fetch latest
git -C ~/ha-mcp-fork checkout addon-repo
git -C ~/ha-mcp-fork fetch upstream
git -C ~/ha-mcp-fork fetch upstream pull/<PR>/head:pr-<PR> --force

# Reset and merge PR
git -C ~/ha-mcp-fork reset --hard upstream/master
git -C ~/ha-mcp-fork merge pr-<PR> --no-ff -m "Merge branch 'pr-<PR>' into addon-repo"

# Restore docs and Fork-Dev addon files
cd ~/ha-mcp-fork
cp ~/.ha-mcp-fork-dev.md FORK-DEV.md
cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/
rm -rf homeassistant-addon-dev/src/ha_mcp
cp -r src/ha_mcp homeassistant-addon-dev/src/ha_mcp
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# Edit homeassistant-addon-dev/config.yaml:
#   - remove `image:` line
#   - name: "Fork-Dev"
#   - url: fork
#   - bump version (or hold if NabuForkDev is the bump target this cycle)

# Edit homeassistant-addon-webhook-proxy/config.yaml:
#   - name: "NabuForkDev"
#   - slug: "ha_mcp_webhook_proxy_dev"
#   - url: fork
#   - hold version (or bump if user requested)

# Apply NabuForkDev coexistence renames (every cycle, since reset wipes them):
cd ~/ha-mcp-fork/homeassistant-addon-webhook-proxy
git mv mcp_proxy mcp_proxy_dev
sed -i 's|COPY mcp_proxy /opt/mcp_proxy|COPY mcp_proxy_dev /opt/mcp_proxy_dev|' Dockerfile
sed -i \
  -e 's|/opt/mcp_proxy|/opt/mcp_proxy_dev|g' \
  -e 's|/config/custom_components/mcp_proxy|/config/custom_components/mcp_proxy_dev|g' \
  -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
  -e 's|"mcp_proxy_restart"|"mcp_proxy_dev_restart"|g' \
  -e 's|domain") == "mcp_proxy"|domain") == "mcp_proxy_dev"|g' \
  -e 's|"handler": "mcp_proxy"|"handler": "mcp_proxy_dev"|g' \
  start.py
sed -i \
  -e 's|DOMAIN = "mcp_proxy"|DOMAIN = "mcp_proxy_dev"|g' \
  -e 's|/config/\.mcp_proxy_config\.json|/config/.mcp_proxy_dev_config.json|g' \
  mcp_proxy_dev/__init__.py mcp_proxy_dev/config_flow.py
# Then by hand: edit mcp_proxy_dev/manifest.json (domain, name) +
# mcp_proxy_dev/strings.json (titles) + mcp_proxy_dev/config_flow.py (titles)
# to "MCP Webhook Proxy (NabuForkDev)" — see step 8e/8f above.
cd ~/ha-mcp-fork

# Add README banner (above existing content):
#   > **This is a personal fork.** See [`FORK-DEV.md`](FORK-DEV.md) for the addon-repo workflow.

git -C ~/ha-mcp-fork add -A
git -C ~/ha-mcp-fork commit -m "chore: reset addon-repo to upstream master + PR #<PR> only, dev<N>"
git -C ~/ha-mcp-fork push origin addon-repo --force
```

## When User Asks to Bump NabuForkDev Instead

If a cycle's request is "bump nabu casa proxy too" / "push NabuForkDev to dev<N>":

1. Same merge + restore steps as above.
2. **Hold** Fork-Dev's `version:` at its current value.
3. **Bump** NabuForkDev's `version:` to the next dev number.
4. Commit message reflects the bump target, e.g., `chore: addon-repo refresh, NabuForkDev dev<N>`.
