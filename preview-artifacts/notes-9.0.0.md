## HA-MCP Custom Component 9.0.0

Installs the ha-mcp 9.0.0 server released with it; restart Home Assistant after updating to run both.

Component changes since v8.6.0:
- 9.0.0
- Merge master into PR 2655, preserving current fixes and paired-release changes
- fix(component): take the component's flow helpers from Core's helper flow list (#2697)
- fix: relay Nabu Casa cloudhooks through the webhook ingress (#2703)
- feat: one dashboard card search that reaches cards nested inside custom cards (#2700)
- feat: compact ha_search_tools results with a full-schema second hop (#2670)
- feat: use native Core contracts for energy and recorder tools (#2685)
- fix: support Home Assistant 2026.11 pip_kwargs() signature (#2690)
- feat: describe and check dashboard cards from Home Assistant's frontend (#2671)
- fix: correct OAuth metadata for read-only webhook connections (#2683)
- Merge remote-tracking branch 'upstream/master' into fix/2655-review
- fix: address the toolkit review of the HACS default-listing PR
- feat: take helper fields, validation and backups from Home Assistant (#2648)
- fix(component): carry the caller's context on component service calls (#2614)
- Merge remote-tracking branch 'upstream/master' into feat/2427-hacs-default-listing
- feat(component): reinstall the server from its package repair
- docs: drop provisioning and auto-update wording left by the paired-release change
- chore(component): leave translated catalogs and the duplicate-code baseline to their sync jobs
- fix(component): refuse userinfo in the loopback callback port exception
- fix(component): let entry removal still delete the old account after a token switch
- fix(component): keep the server entry's device and its version current
- docs: correct dev tag timing and addon-publish trigger after binary removal (#2651)
- fix(component): let an abandoned server setup not block the next one
- feat(component)!: run the in-process server with an administrator's own token
- fix(component): name each Configure callback row in the singular
- fix(settings): stack the list editor's controls and show failed saves as errors
- feat(component)!: none-mode OAuth redirects only to allowlisted callbacks
- fix(component): stop calling the secret-URL auth mode the default
- feat(component): new server entries start with the webhook off and loopback-only
- feat(component): share the server's version and pin its build in every HACS release
- fix: update a zone's stored icon and refuse clearing it (#2643)
- feat(component)!: run the server each component release pins instead of self-updating it
- fix(component): keep credentials out of the startup log and drop the HACS-internals hooks
- feat: Home Assistant 2026.10 MCP discovery and tool metadata (#2613)
- perf: fold simple-helper fields into config and route helper writes through the component (#2618)
- refactor: split the component's websocket_api.py into a package (#2628)
- refactor: bring five long functions under the statement limit (#2612)
- chore: enable PLR0915 and mark the existing long functions (#2610)
- chore: enable BLE001 and mark the existing blind excepts (#2609)

Full server changelog: https://github.com/homeassistant-ai/ha-mcp/releases
