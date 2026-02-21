# First-Call Docs: Progressive Disclosure via FastMCP 3.0 Provider

## Status: Research / Pre-Implementation

Research conducted 2026-02-21. This document captures findings, intent, and design
direction for a new approach to idle context reduction using FastMCP 3.0's Provider
abstraction.

---

## The Problem

ha-mcp registers 86+ tools. Their combined descriptions consume **~45K tokens** of
idle context — loaded on every LLM turn regardless of which tools are used. This is:

- **17.8% of Claude's context** on every single turn
- **27.8% of GPT-4o's context**
- Well over ChatGPT's hard 16K tool token limit (#614)

The descriptions are this large because they contain extensive documentation, examples,
troubleshooting guides, and format specifications — all to prevent LLMs from misusing
the tools. Without this documentation, LLMs (especially Haiku, Sonnet, and non-Anthropic
models) make frequent structural mistakes.

## Prior Approaches and Their Limitations

### PR #616 — Progressive Disclosure with `guide_response` (Closed)

**What it did:** Stripped tool descriptions to one-liners. Added a required
`guide_response` parameter to each thinned tool. LLMs were forced to call
`ha_get_tool_guide("topic")` before using any thinned tool, and pass the guide
response back as a parameter.

**Why it was abandoned:** The forced guide reading on every tool call inflated token
usage. The LLM had to read the full guide, pass it back as `guide_response`, and the
server had to validate it. This added significant per-call overhead that negated the
idle context savings.

**Token math:** ~81% reduction in idle tokens, but substantial increase in per-call
tokens from mandatory guide round-trips.

### PR #637 — Category Gateway Proxy (Open)

**What it did:** Consolidated 12 dashboard tools behind a single gateway tool
(`ha_manage_dashboards`). LLMs call the gateway with `tool="ha_config_set_dashboard"`
and `args={...}`. Uses a `_MockMCP` class to intercept `@mcp.tool()` registrations
without modifying tool modules.

**Limitations:**
- Works well with Opus but **Sonnet, Haiku, and other LLMs struggle** with the
  gateway dispatch pattern (`tool` + `args` string dispatch)
- `_MockMCP` is a clever hack but not idiomatic
- Manual parameter validation in the gateway handler (reimplements what FastMCP does)
- Only saves tokens for the consolidated category (12 → 1 tool)

## FastMCP 3.0 — What's New and Relevant

FastMCP 3.0.0 was released 2026-02-18. The following features are relevant to this
effort. All are merged and shipping.

### Provider Abstraction (PR #2622 — Most Important)

Providers have three methods per component type: **list, get, and execute**.

```python
class Provider:
    async def list_tools(self, context):      # Controls what tools/list returns
    async def get_tool(self, context, name):   # Resolves tool lookup
    async def call_tool(self, context, name, arguments):  # Intercepts execution
```

The `context` parameter provides access to **session state**. This is the key enabler:
a Provider can change behavior per-session, per-tool, per-call — all server-side, all
transparent to the client.

**Reference:** https://github.com/PrefectHQ/fastmcp/pull/2622

### Session State (PR #2917)

`ctx.set_state(key, value)` / `ctx.get_state(key)` persist data across tool calls
within a session. Each client session is isolated.

```python
await ctx.set_state("docs_seen_dashboard", True)
later = await ctx.get_state("docs_seen_dashboard")  # True
```

This enables tracking what the LLM has seen, how many times it's tried a tool, etc.

**Reference:** https://github.com/PrefectHQ/fastmcp/pull/2917

### Session Visibility Control (PR #2917)

`ctx.enable_components()` / `ctx.disable_components()` for per-session tool visibility.
Requires `tools/list_changed` client support — not universal. Useful as a bonus layer
for capable clients but not reliable as the primary mechanism.

Could be used for **auto-detecting client capabilities at connection time** and serving
"lite" vs "full" tool sets before `tools/list` is ever called.

**Reference:** https://github.com/PrefectHQ/fastmcp/pull/2917

### Transform System (PR #2836)

Transforms modify components as they flow from providers to clients. `ToolTransform`
can rename tools, rewrite descriptions, modify argument schemas. Visibility is
implemented as a transform, proving transforms can be session-aware.

Operates server-side — clients see only the transformed output. Universal compatibility.
Static by default but the infrastructure supports session-aware behavior (visibility
transform proves this).

**Reference:** https://github.com/PrefectHQ/fastmcp/pull/2836

### FileSystemProvider (PR #2823)

Discovers tools/resources/prompts from Python files in a directory. `reload=True` for
development hot-reload. Could store full tool documentation as files loaded on demand.

**Reference:** https://github.com/PrefectHQ/fastmcp/pull/2823

### SkillsProvider (PR #2944)

Exposes agent skill directories (SKILL.md files) as MCP resources. ha-mcp has an
existing `skills.md` that's optional for users. Could bundle tool documentation as
skills — clients that support resources get full docs, others get the truncated version.

Skills are broken down per-directory so only relevant portions are loaded, not the
entire file.

Vendor-specific providers: ClaudeSkillsProvider, CursorSkillsProvider, etc.

**Reference:** https://github.com/PrefectHQ/fastmcp/pull/2944

### Resource & Prompt Meta (PRs #2611, #2610)

Resources and prompts can carry structured metadata via `.meta` field. Potentially
useful for tagging documentation resources with tool associations.

**References:**
- https://github.com/PrefectHQ/fastmcp/pull/2611
- https://github.com/PrefectHQ/fastmcp/pull/2610

## The First-Call Docs Concept

### Core Idea

Use FastMCP 3.0's Provider `call_tool()` with session state to force LLMs to see
full documentation the **first time** they use each tool — then never again for the
rest of the session. No forced parameters, no guide reading tool, no gateway dispatch.

### How It Works

1. **Tools registered with compressed descriptions** — one-liners that tell the LLM
   WHEN to use the tool. Full parameter schemas remain intact.

2. **First call to any tool** — Provider's `call_tool()` checks session state. If the
   LLM hasn't seen docs for this tool yet, it returns the full documentation instead
   of executing. Sets session state marking docs as delivered.

3. **Subsequent calls** — Provider sees docs were already delivered, executes normally.

4. **Result:** One wasted round-trip per tool per session. After that, zero overhead.

### Implementation Sketch

```python
class FirstCallDocsProvider(Provider):
    """Provider that forces documentation delivery on first tool use."""

    async def list_tools(self, context):
        # Return tools with compressed descriptions + full parameter schemas
        return self.compressed_tools

    async def call_tool(self, context, name, arguments):
        docs_key = f"docs_seen_{name}"
        docs_seen = await context.get_state(docs_key)

        if not docs_seen and name in self.documented_tools:
            # First call: return full documentation, don't execute
            await context.set_state(docs_key, True)
            return {
                "status": "documentation_required",
                "tool": name,
                "documentation": self.full_docs[name],
                "message": "Review the documentation above, then call this tool again."
            }

        # Subsequent calls: execute normally
        return await self._execute(name, arguments)
```

### Token Math (Estimated)

```
Current (master):
  Every turn: ~45K tokens idle (all tool descriptions)

First-Call Docs:
  Every turn: ~10-15K tokens idle (compressed descriptions)
  First use of each tool: ~800-3500 tokens one-time (full docs in response)
  Subsequent uses: 0 additional tokens

Savings: ~67-78% reduction in idle context
```

### Comparison to Prior Approaches

| Aspect | #616 (guide_response) | #637 (gateway) | First-Call Docs |
|--------|----------------------|-----------------|-----------------|
| Idle token cost | ~8.5K | ~35K (saves only dashboard) | ~10-15K |
| Per-call overhead | Every call (forced guide) | Every call (dispatch) | First call only |
| LLM compatibility | All clients | Opus good, Sonnet/Haiku struggle | All clients (native tools) |
| Tool code changes | Yes (add guide_response param) | No | No |
| Framework dependency | None | None | FastMCP 3.0 Provider |
| Parameter validation | Manual (guide_response) | Manual (gateway dispatch) | Native FastMCP |
| Hack/workaround needed | validate_guide_response() | _MockMCP class | None — uses official API |

### Advantages

- **Works on every client** — no `tools/list_changed` dependency
- **Tools are native MCP tools** — LLMs call them normally with typed parameters
- **No gateway dispatch** — Sonnet/Haiku don't struggle with `tool` + `args` pattern
- **No forced guide reading** — no extra parameter, no separate guide tool call
- **One round-trip per tool per session** — not per call like #616
- **No tool module changes** — descriptions compressed externally via Provider
- **Session-aware** — uses official FastMCP 3.0 session state API
- **Stacks with server-side validation** — Provider `call_tool()` can validate AND
  deliver docs (see #659)

### Open Questions

1. **Does `list_tools(context)` carry client info?** If so, the Provider could
   auto-detect the client and serve lite/full descriptions without any toggle.

2. **Can custom Transforms access session context?** Visibility is a session-aware
   transform. If custom transforms can too, description compression could be dynamic
   per-session rather than static.

3. **Which tools need first-call docs?** Not all 86+ tools have complex descriptions.
   Many (like `ha_get_overview`) are simple enough that the compressed description
   suffices. Focus on the tools with >500 token descriptions.

4. **Should first-call docs block execution or execute-and-include-docs?**
   - Block: forces the LLM to read before acting. Safer for destructive tools.
   - Execute + include: no wasted round-trip but LLM might ignore docs in response.
   - Could vary per tool based on `destructiveHint` annotation.

5. **Interaction with SkillsProvider:** Could full docs live as skill resources for
   clients that support resources, while first-call docs serve as the universal
   fallback? "Full" vs "lite" mode based on client capabilities.

6. **Interaction with server-side validation (#659):** First-call docs teaches format.
   Server-side validation catches mistakes. Together they provide defense in depth.
   Could share the Provider `call_tool()` implementation. Separate PRs recommended —
   each stands on its own merit.

## FastMCP 3.0 Features Investigated But Not Directly Applicable

### `tools/list_changed` Notification

Clients that support this can dynamically show/hide tools mid-session. However,
support is not universal:

| Client | Supports `tools/list_changed` |
|--------|------------------------------|
| Claude Code | Yes |
| Cursor (v0.42+) | Yes |
| GitHub Copilot | Yes |
| claude.ai (web) | Likely no — docs say "advanced capabilities not yet supported" |
| Claude Desktop | Unknown — old info says no, unconfirmed |
| ChatGPT | Unknown |
| Gemini CLI | No (open feature request) |

**Verdict:** Cannot be the primary mechanism. Could be a bonus layer for capable
clients.

### ToolTransform (Static Description Compression)

Can rewrite tool descriptions server-side before `tools/list` is sent. Universal
client compatibility. But **static only** — same compressed description every time.
Equivalent to editing tool descriptions in source code, just without touching the
source.

Could be used alongside First-Call Docs for the static compression layer.

### Dynamic Description Changes

No mechanism exists in FastMCP 3.0 or the MCP protocol to dynamically change tool
descriptions mid-session without `tools/list_changed` client support. The tool
descriptions in `tools/list` are fetched once at connection time by most clients.

This is why First-Call Docs works through `call_tool()` responses rather than trying
to change what's in `tools/list`.

## Related Issues and PRs

- **#659** — Server-side validation for tool arguments (complementary, separate PR)
- **#657** — FastMCP 3.0 upgrade (prerequisite — enables Provider-based approach)
- **#637** — Category gateway proxy (alternative approach, Opus-focused)
- **#616** — Progressive disclosure with guide_response (closed, predecessor)
- **#614** — ChatGPT tool token limit (motivating issue)
- **#567** — Context exhaustion from tool responses

## Current ha-mcp Validation State

For reference, here's what's validated server-side today vs what's not:

**Validated:**
- JSON parsing + type checking (all tools)
- Required fields for automations (`alias`, `trigger`, `action`)
- Field name normalization (`triggers` → `trigger`)
- Python sandbox with full AST validation (dashboard transforms)
- jq transform error handling
- config_hash optimistic locking (dashboards)
- Parameter coercion (bools, ints, lists)
- 38 structured error codes

**Not validated (sent directly to HA):**
- Jinja2 template syntax
- Automation trigger/condition/action internal structure
- Lovelace card structure
- Entity ID existence in automations
- Service name validity in actions

## Intent

The goal is to implement First-Call Docs as a new PR that:

1. Requires FastMCP 3.0 (#657 merged first)
2. Uses the official Provider `call_tool()` API — no hacks
3. Compresses tool descriptions for idle context reduction
4. Forces full documentation delivery on first use per tool per session
5. Works on every MCP client universally
6. Does not modify existing tool module code
7. Can be combined with server-side validation (#659) in the future
8. Could optionally integrate with SkillsProvider for "full" vs "lite" client modes
