# fork-dev-scripts

Tooling that lives only on the `addon-repo` branch of this fork. Nothing here
ships with the add-on Docker images — these scripts are for maintainers
managing the addon-repo refresh cycle.

## Protected across resets

`addon-repo` is force-pushed on every PR test cycle by
`apply-fork-overrides.sh`, which does `git reset --hard upstream/master` and
then re-merges a single PR. The reset would normally wipe everything not in
`upstream/master`, but the script stashes this folder to a temp dir before
the reset and restores it afterwards, so the contents always travel with
the branch.

That means you can clone the fork on any machine, check out `addon-repo`,
and run `./fork-dev-scripts/apply-fork-overrides.sh ...` without needing any
local backup files.

## Contents

| Path | Purpose |
|---|---|
| `apply-fork-overrides.sh` | The addon-repo refresh script. Fetches a PR, resets `addon-repo` to `upstream/master + PR`, re-applies Fork-Dev + NabuForkDev overrides, bumps the dev version, force-pushes. Use `--help` for full options. |
| `FORK-DEV.md` | Canonical copy of the FORK-DEV.md doc that gets restored to the repo root after every reset. Edit this copy (or the live root copy — the script syncs root → here at the end of each cycle). |
| `Mock Stable/` | Helpers that swap `homeassistant-addon-dev/` between its dev flavor and a stable-mirroring "stable test" flavor. See the README inside the folder. |

## Typical use

```bash
# Standard cycle (latest PR #1234, bump Fork-Dev version):
./fork-dev-scripts/apply-fork-overrides.sh --pr 1234 --bump fork-dev

# Bump NabuForkDev instead:
./fork-dev-scripts/apply-fork-overrides.sh --pr 1234 --bump nabu

# Two PRs stacked, bump both addons:
./fork-dev-scripts/apply-fork-overrides.sh --pr 1234 --pr 1240 --bump both
```

## Adding more tooling here

Anything you drop into `fork-dev-scripts/` (top-level files or nested
folders) survives the addon-repo refresh automatically. No script changes
needed.
