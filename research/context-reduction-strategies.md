# ha-mcp Context Reduction Research

**Date:** 2026-02-14
**Related Issues:** #614, #567, #605
**Related PR:** #616 (progressive disclosure / `ha_get_tool_guide`)
**Status:** Preliminary research — no implementation yet

---

## Key Insight: Round-Trip Token Cost Is a Red Herring

The proxy/meta-tool approaches (Tool Search, Semantic Search) add 2-3 extra round trips per tool invocation. The instinct is that this "costs more tokens." **It doesn't — it saves massively.**

### The Math

**Current system (96 tools always loaded):**
```
Every API turn pays ~35,000 tokens for tool definitions sitting in context.
20-turn conversation: 20 × 35K = ~700K tokens just for idle tool defs.
```

**Proxy pattern (3 meta-tools + on-demand lookups):**
```
Every API turn pays ~2K tokens for 3 meta-tool definitions.
20-turn conversation: 20 × 2K = ~40K tokens for tool defs.
5 on-demand schema lookups × ~1K each = ~5K tokens.
Total: ~45K tokens.
```

**Result: ~93% fewer tokens overall.** The extra round trips add a small amount of tokens for the schema lookups, but this is dwarfed by eliminating 35K tokens of dead weight from every single turn. The dominant cost in the current system is paying for 96 tool definitions on every turn regardless of whether any of them get used.

---

## The Problem

ha-mcp v6.6.1 registers 96 tools. Their combined definitions consume:

| Metric | Value |
|--------|-------|
| Total tokens (compact JSON) | ~35,500 |
| Total characters | ~89,000 |
| Context consumed on Claude (200K) | 17.8% |
| Context consumed on GPT-4o (128K) | 27.8% |
| Cost per request on Opus ($15/M input) | $0.53 |

### Where tokens go:
| Component | Tokens | % |
|-----------|--------|---|
| Tool descriptions | 21,487 | 48% |
| Parameter schemas | 18,215 | 41% |
| Annotations | 2,382 | 5% |
| Structural JSON overhead | 2,101 | 5% |
| Tool names | 537 | 1% |

This creates two problems:
1. **Idle context waste** — all 96 tools are loaded on every turn even when unused
2. **Client-specific hard limits** — ChatGPT has a ~16K token limit for tool definitions (per #614 reporter), making ha-mcp completely unusable there

---

## Approaches Researched

### 1. PR #616: Progressive Disclosure (`ha_get_tool_guide`)

**What it does:** Trims the 10 most verbose tool descriptions, moves full docs to an on-demand `ha_get_tool_guide()` meta-tool. Adds a required `guide_response` parameter to enforce the LLM reads the guide before calling the tool.

**Results:** ~89K chars → ~68K chars (24% reduction on 10 tools). If expanded to all 96 tools, reduction would be much larger.

**Pros:**
- Works with every client (no special MCP features needed)
- No architectural change to how tools are registered
- Already implemented and passing CI

**Cons:**
- The `guide_response` required parameter creates a multi-step workflow (call guide → pipe output to tool) that weaker models (Qwen-7B, small Llama, etc.) may not follow
- If the LLM ignores the guide instruction, it gets a one-liner description with no useful context — hard failure, no graceful degradation
- Even fully expanded, still may not fit within ChatGPT's tool token limit

**Maintainer feedback:** Concern about tool descriptions being "deleted or overlooked by the AI." Wants LLM testing before merge.

### 2. Tool Search Tool Pattern (Proxy)

**What it does:** Replace 96 individual tool registrations with 2-3 meta-tools:
```
ha_search_tools(query, category?)  → returns matching tool names + descriptions
ha_get_tool_schema(tool_name)      → returns full schema + docs for one tool
ha_execute_tool(tool_name, args)   → proxies the call to the real tool
```

**Results:** ~1-2K tokens idle (3 tool definitions) vs ~35K today. **95%+ reduction.**

**Pros:**
- Works with every client and every model — just standard tool calls with simple parameters
- Scales to any number of tools without growing context
- Each step is a simple tool call (no multi-step piping like `guide_response`)
- Even Qwen-7B can call `ha_search_tools("automation")`
- Matches Anthropic's own recommended pattern for large tool libraries

**Cons:**
- Every tool call becomes 2-3 round trips (search → schema → execute), adding latency
- `ha_execute_tool` takes freeform JSON args — MCP client can't validate against schema
- Bigger architectural change (need tool registry, search index, proxy dispatcher)
- LLM must correctly construct args from a schema it read a turn earlier (not inline)

**Token math:** Extra round trips cost far less than loading all tools idle (see Key Insight above).

**References:**
- [Anthropic engineering blog on code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) — Anthropic's recommended approach
- Anthropic saw Opus 4 accuracy improve from 49% to 74% with tool search
- 85% token reduction while maintaining full tool access

### 3. Semantic Search over Tool Embeddings

**What it does:** Same as #2 but uses vector embeddings instead of keyword/prefix search:
```
ha_find_tools("I want to create an automation that turns on lights at sunset")
→ returns best matching tools ranked by semantic similarity
```

**Results:** ~2K initial tokens. Even lower than prefix-based search.

**Pros:**
- Most natural for LLMs — describe what you want in plain language
- Lower initial tokens than prefix search (~1,300 vs ~2,500 per Speakeasy benchmarks)

**Cons:**
- Requires an embedding model and index (though for 96 tools this is tiny — could be precomputed)
- Embedding quality is critical — missed tools = silent failures
- Less deterministic than explicit browsing
- Extra dependency

**References:**
- [Speakeasy - 100x token reduction with dynamic toolsets](https://www.speakeasy.com/blog/100x-token-reduction-dynamic-toolsets)

### 4. Hybrid: Core Tools + Proxy for the Rest

**What it does:** Keep 10-15 most-used tools registered normally with full MCP schemas. Proxy the remaining 80+ tools through meta-tools.

```
Always loaded (normal MCP tools with full schemas):
  ha_search_entities, ha_get_state, ha_get_entity, ha_call_service,
  ha_set_entity, ha_get_overview, ha_eval_template, ...
  ha_find_tools(query)        ← discovers the rest
  ha_execute_tool(name, args) ← proxies the rest

Not loaded until discovered via ha_find_tools:
  ha_config_set_automation, ha_config_set_dashboard, ha_config_set_script,
  ha_get_history, ha_get_statistics, ha_manage_backups, ... (80+ tools)
```

**Pros:**
- Schema validation preserved for the most common tools
- Massive context reduction for the long tail
- Works with every client and every model
- Essentially what Anthropic's `defer_loading` does, but implemented server-side so it's universal

**Cons:**
- Still a significant architectural change
- Need to decide which tools are "core" vs "proxied"

### 5. Dynamic Tool Registration (`listChanged`)

**What it does:** Start with minimal tools, dynamically register/unregister tool modules at runtime. Emit `notifications/tools/list_changed` when tools change.

**Client support (as of Feb 2026):**
| Client | Supports `listChanged`? |
|--------|------------------------|
| Claude Code | Yes (confirmed in docs) |
| Claude Desktop | Unknown — last confirmed "no" was Jul 2025, discussion closed |
| Claude.ai | Unknown — no technical docs found |
| ChatGPT | No — requires manual "Refresh" button |
| Qwen Code | No mention in docs |
| Gemini CLI | No — open issue requesting it |
| GitHub Copilot | Yes |

**Verdict:** Too many major clients don't support it to rely on as a primary strategy.

### 6. `ENABLED_TOOL_MODULES` (Static Server Config)

**What it does:** User sets which tool modules to load at startup via addon config.

**Pros:** Universal compatibility, zero complexity.
**Cons:** User must know what they need. Static — can't adapt per session. Requires server restart to change. Not AI-controlled.

**Verdict:** Useful as a last-resort escape hatch for hard-limited clients (ChatGPT), but too clunky as a primary solution.

### 7. `defer_loading` (Claude API Feature)

**What it does:** Tools marked `defer_loading: true` are withheld from Claude's context by Anthropic's API. Claude uses a built-in Tool Search Tool to discover them on demand.

**Key finding:** This is a **Claude API/platform feature**, NOT an MCP protocol feature. All tool definitions are still sent to the API — Anthropic's server-side infrastructure handles the filtering. Only works with Claude (Sonnet 4+, Opus 4+, no Haiku). Not usable by any other LLM.

**References:**
- [Claude API - Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- [Anthropic - Introducing advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)

---

## Comparison Matrix

| Approach | Token Reduction | Works All Models | Works All Clients | Complexity | Round Trips |
|----------|----------------|-----------------|-------------------|------------|-------------|
| PR #616 (guide tool) | ~24% (expandable) | Strong models only | Yes | Low | +1 per guided tool |
| Tool Search Proxy | ~95% | Yes | Yes | High | +2-3 per call |
| Semantic Search | ~95%+ | Yes | Yes | High | +1-2 per call |
| Hybrid (core + proxy) | ~80-90% | Yes | Yes | Medium-High | +2-3 for proxied tools |
| Dynamic registration | ~60-80% | Yes | Only `listChanged` clients | Medium | +1 to load domain |
| `ENABLED_TOOL_MODULES` | Variable | Yes | Yes | Trivial | 0 |
| `defer_loading` | ~95% | Claude only | Claude API only | Low (client-side) | Built-in |

---

## Recommended Direction

**Primary approach: Tool Search Proxy (Option 2) or Hybrid (Option 4)**

These provide the largest universal reduction while working across all clients and models. The extra round trips are a net token savings, not a cost (see Key Insight section).

The Hybrid approach preserves schema validation for the most commonly used tools while dramatically reducing context for the long tail — essentially a server-side implementation of what `defer_loading` does for Claude only.

**#616 as complementary:** The progressive disclosure pattern from PR #616 can still be applied to the core tools that remain always-loaded in the hybrid approach, further reducing their description sizes.

**`ENABLED_TOOL_MODULES` as escape hatch:** Expose in addon config for users on hard-limited clients (ChatGPT) who need to manually reduce tool count.

---

## Community Proposals & References

- [MCP Discussion #532 - Hierarchical Tool Management](https://github.com/orgs/modelcontextprotocol/discussions/532) — Proposed spec extension (not adopted yet)
- [MCP Discussion #76 - listChanged support](https://github.com/orgs/modelcontextprotocol/discussions/76) — Client support tracking
- [Speakeasy - Progressive Discovery vs Semantic Search](https://www.speakeasy.com/blog/100x-token-reduction-dynamic-toolsets) — 100x token reduction benchmarks
- [Klavis - 4 MCP Design Patterns](https://www.klavis.ai/blog/less-is-more-mcp-design-patterns-for-ai-agents) — Semantic search, workflow-based, code mode, progressive discovery
- [Anthropic - Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) — Tool search tool pattern
- [Anthropic - Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — Minimum context principle
- [Merge - MCP Tool Description Guide](https://www.merge.dev/blog/mcp-tool-description) — 1-2 sentence descriptions recommended
- [Philipp Schmid - MCP Best Practices](https://www.philschmid.de/mcp-best-practices) — Context window as finite resource

---

## Next Steps

1. Investigate FastMCP's support for dynamic tool registration and proxy patterns
2. Prototype the hybrid approach (core tools + proxy) in a branch
3. Test with multiple models (Claude, Qwen, GPT) to validate compatibility
4. Measure actual token usage with the proxy pattern vs current baseline
5. Decide on search implementation (keyword/prefix vs semantic embeddings)
