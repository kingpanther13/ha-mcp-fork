# Live-iteration dev harness

`scripts/dev_harness.py` lets you call any ha-mcp tool against a running test
HA instance in a way that picks up source edits immediately — no MCP server
to restart, no `/mcp` reconnect, no daemon to babysit.

How it works: every invocation spawns a fresh `uv run ha-mcp` subprocess,
drives it over stdio JSON-RPC for one call, and tears it down. The next call
starts a brand new subprocess that re-imports the current source. Edit a
file, run the harness, see the new behavior.

This is the right tool when you're actively editing tool code and want to
verify behavior in a tight loop. For day-to-day "use HA from chat" the
regular `ha-mcp` MCP server registration is fine.

## One-time setup

Bring up a test Home Assistant container and keep it around. The repo ships
`hamcp-test-env` for exactly this:

```bash
# From the repo root, with a clean checkout (uv sync done):
HA_TEST_PORT=32769 nohup uv run hamcp-test-env --no-interactive \
    > /tmp/hamcp-test-env.log 2>&1 & disown
```

Wait for the API to come up:

```bash
TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIxOTE5ZTZlMTVkYjI0Mzk2YTQ4YjFiZTI1MDM1YmU2YSIsImlhdCI6MTc1NzI4OTc5NiwiZXhwIjoyMDcyNjQ5Nzk2fQ.Yp9SSAjm2gvl9Xcu96FFxS8SapHxWAVzaI0E3cD9xac
until curl -fsS -m 3 http://localhost:32769/api/ -H "Authorization: Bearer $TOKEN" >/dev/null; do
  sleep 4
done
echo ready
```

The token above is the public test token from `tests/test_constants.py` —
it's pre-baked into the test container's auth storage and expires in 2035.

The container persists across iterations of ha-mcp; you do not need to
recreate it when you change source.

## Usage

```bash
# List every tool the current source exposes:
uv run python scripts/dev_harness.py tools

# Call any tool. k=v pairs are JSON-decoded when possible:
uv run python scripts/dev_harness.py call ha_search_entities query=light limit=3
uv run python scripts/dev_harness.py call ha_get_state entity_id=light.bed_light
uv run python scripts/dev_harness.py call ha_config_set_helper \
    helper_type=input_number name=DEMO min_value=0 max_value=100 initial=42

# Verify HA state directly (bypasses ha-mcp), useful when you don't trust
# the tool's success: true alone:
uv run python scripts/dev_harness.py state input_number.demo
uv run python scripts/dev_harness.py helper-config input_number demo

# End-to-end smoke check (lists tools, calls one):
uv run python scripts/dev_harness.py smoke
```

The iteration loop is just: edit `src/ha_mcp/...` → re-run the harness command.
The new code is loaded on the next subprocess spawn.

## Environment overrides

| Variable | Default | Purpose |
| --- | --- | --- |
| `HA_TEST_HOST` | `localhost` | Test HA hostname |
| `HA_TEST_PORT` | `32769` | Test HA port (must match `--port`/`HA_TEST_PORT` you used to start `hamcp-test-env`) |
| `HOMEASSISTANT_TOKEN` | the public test token | Override if you point at a non-test HA |

## Switching versions

Worktrees make this trivial — the harness uses `Path(__file__).resolve().parents[1]`
to find the repo root, so it always runs against the source in its own checkout.

```bash
# Test branch A:
git worktree add worktree/branch-a branch-a
(cd worktree/branch-a && uv sync && uv run python scripts/dev_harness.py smoke)

# Test branch B side-by-side, against the same test HA:
git worktree add worktree/branch-b branch-b
(cd worktree/branch-b && uv sync && uv run python scripts/dev_harness.py smoke)
```

Both worktrees talk to the same `localhost:32769` HA, so the comparison is
apples-to-apples on backend state.

## Troubleshooting

- **`MCP server closed stdout` with import errors in stderr**: run `uv sync`
  in the worktree first.
- **WebSocket connection refused**: the test HA isn't running. Check
  `/tmp/hamcp-test-env.log` and re-run the `hamcp-test-env` command above.
- **`auth_invalid` from the test HA**: the container was recreated with a
  different storage seed. Pull `tests/test_constants.py` for the current
  token, or set `HOMEASSISTANT_TOKEN` explicitly.
- **Subprocess takes ~2s per call**: that's `uv run` + FastMCP init. It's the
  cost of having truly stateless iteration. If you want lower steady-state
  latency, run a long-lived ha-mcp HTTP server and hit it with a client —
  but you'll then need to restart it on source changes.
