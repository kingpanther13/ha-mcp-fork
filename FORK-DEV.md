# Fork Development Workflow

This branch (`addon-repo`) is the **default branch** on this fork. It serves as the HA add-on repository for testing PR branches on a real Home Assistant instance.

**PRs are NOT based on this branch.** Feature branches go to `upstream/master` independently. This branch is always force-pushed to mirror whatever feature branch is being tested.

> **A backup of this file lives at `~/.ha-mcp-fork-dev.md`.**
> If `git reset --hard` wipes it, restore with: `cp ~/.ha-mcp-fork-dev.md ~/ha-mcp-fork/FORK-DEV.md`

## How It Works

1. HA Supervisor clones this repo's default branch (`addon-repo`)
2. It finds `homeassistant-addon-dev/config.yaml` and builds the Docker image from `homeassistant-addon-dev/Dockerfile`
3. The addon runs the code from `homeassistant-addon-dev/src/ha_mcp/`

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
cd ~/ha-mcp-fork
git checkout addon-repo

# Reset to the feature branch
git reset --hard <feature-branch>
```

### After `git reset --hard` - CRITICAL STEPS

The reset wipes addon-specific files that don't exist on feature branches. You **must** restore them:

```bash
# 1. Restore this documentation (gets wiped by reset!)
cp ~/.ha-mcp-fork-dev.md FORK-DEV.md

# 2. Restore the README banner
#    Add at the very top of README.md:
#    > **This is a personal fork.** See [`FORK-DEV.md`](FORK-DEV.md) for the addon-repo workflow.

# 3. Copy build files into the addon directory
cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/

# 4. Copy source code (the critical sync step!)
cp -r src/ha_mcp/* homeassistant-addon-dev/src/ha_mcp/

# 5. Fix the Dockerfile (upstream references wrong path)
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# 6. Update config.yaml:
#    - Remove the `image:` line (forces local build instead of pulling from ghcr.io)
#    - Set name to "Fork-Dev" (distinguishes from official addon)
#    - Bump version (forces HA Supervisor to rebuild)
```

### Why These Files Are Needed

| File | Why |
|------|-----|
| `homeassistant-addon-dev/pyproject.toml` | Dockerfile `COPY pyproject.toml` - needed for `uv sync` |
| `homeassistant-addon-dev/uv.lock` | Dockerfile `COPY uv.lock` - pinned dependencies |
| `homeassistant-addon-dev/start.py` | Dockerfile `COPY start.py /` - addon entrypoint |
| `homeassistant-addon-dev/src/ha_mcp/` | Dockerfile `COPY src/` - the actual server code |
| `homeassistant-addon-dev/Dockerfile` | Must use `COPY start.py /` not `COPY homeassistant-addon/start.py /` |
| `FORK-DEV.md` | This file - backup at `~/.ha-mcp-fork-dev.md` |

The upstream Dockerfile is designed for CI builds where the build context is the repo root. When HA Supervisor builds locally, the build context is `homeassistant-addon-dev/` itself, so all paths must be relative to that directory.

### config.yaml: `image` Field

- **With `image:` field**: HA pulls a pre-built image from ghcr.io (upstream code, NOT your branch)
- **Without `image:` field**: HA builds locally from the Dockerfile (your branch code)
- For testing fork branches, the `image:` field **must be removed**

## Forcing a Rebuild

HA Supervisor only rebuilds when the version changes. After pushing changes:

```bash
# Bump version in homeassistant-addon-dev/config.yaml
# e.g., dev5 -> dev6

# Force-push (safe - this branch is never a PR base)
git add -A && git commit -m "chore: sync addon-repo" && git push origin addon-repo --force
```

Then in HA: Settings > Add-ons > Fork-Dev > Rebuild

## Full Deploy Workflow (Copy-Paste)

```bash
cd ~/ha-mcp-fork
git checkout addon-repo
git reset --hard <feature-branch>

# Restore docs and addon-specific files
cp ~/.ha-mcp-fork-dev.md FORK-DEV.md
cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/
cp -r src/ha_mcp/* homeassistant-addon-dev/src/ha_mcp/
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# Edit config.yaml: remove image field, set name "Fork-Dev", bump version
# Then:
git add -A
git commit -m "chore: sync addon-repo to <feature-branch>"
git push origin addon-repo --force
```
