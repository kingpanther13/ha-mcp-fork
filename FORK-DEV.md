# Fork Development Workflow

This branch (`addon-repo`) is the **default branch** on this fork. It serves as the HA add-on repository for testing PR branches on a real Home Assistant instance.

**PRs are NOT based on this branch.** Feature branches go to `upstream/master` independently. This branch is always force-pushed to mirror whatever feature branch is being tested.

## How It Works

1. HA Supervisor clones this repo's default branch (`addon-repo`)
2. It finds `homeassistant-addon-dev/config.yaml` and builds the Docker image from `homeassistant-addon-dev/Dockerfile`
3. The addon runs the code from `homeassistant-addon-dev/src/ha_mcp/`

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
# 1. Copy build files into the addon directory
cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/

# 2. Copy source code
cp -r src/ha_mcp/* homeassistant-addon-dev/src/ha_mcp/

# 3. Fix the Dockerfile (upstream references wrong path)
#    Change: COPY homeassistant-addon/start.py /
#    To:     COPY start.py /
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# 4. Update config.yaml
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

# Restore addon-specific files
cp pyproject.toml homeassistant-addon-dev/
cp uv.lock homeassistant-addon-dev/
cp homeassistant-addon/start.py homeassistant-addon-dev/
cp -r src/ha_mcp/* homeassistant-addon-dev/src/ha_mcp/
sed -i 's|COPY homeassistant-addon/start.py|COPY start.py|' homeassistant-addon-dev/Dockerfile

# Edit config.yaml: remove image field, set name, bump version
# Then:
git add -A
git commit -m "chore: sync addon-repo to <feature-branch>"
git push origin addon-repo --force
```
