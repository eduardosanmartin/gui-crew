# Proposal: observability.py — Real-Time Execution Dashboard

## Intent

`/observability` renders a placeholder; users can't see what crews do while running. `crew_engine.py` emits 15+ event types via `BridgeListener` but nothing renders them. This change implements `observability.py` to display crew execution across three layers (macro/meso/micro) plus resource consumption and error visibility.

## Scope

### In Scope
- **Macro** (1.29): pipeline visualization, task states, progress bar, status bar
- **Meso** (1.30): agent cards, tool invocations, delegations, memory/knowledge ops
- **Micro** (1.31): token streaming, thinking/answer distinction, tokens/sec
- **Resource** (1.32): per-task tokens, cost, duration, iterations
- **Error** (1.33): errors with tracebacks, retry counters
- **crew_id filtering** (1.34): mandatory per-connection filtering
- **Reconnect buffer** (1.35): time-based window (60s), replay on reconnect
- `app.py` bridge: `crew_event_bus = Event[ProtocolEvent]` wiring engine callback

### Out of Scope
- Virtual scrolling for >10k tokens (mitigation: `ui.markdown()` max-height; defer)
- Live cost approximation during streaming (decision: show "—" until exact resource.update)
- Historical execution replay (covered by operations-toolkit)

## Capabilities

### New Capabilities
None — `crew-observability` spec already exists.

### Modified Capabilities
- `crew-observability`: formalize crew_id filtering + reconnect buffer replay as requirements; implement all 5 observation layers

## Approach

Single `observability.py` (~470 LOC) matching `canvas.py` / `operations.py` pattern. Module-level state dicts. `@ui.refreshable` for macro/meso/resource/error; direct mutation for token stream.

`app.py` adds 3-line bridge. `crew_engine.py` stays NiceGUI-free.

**3 PRs (feature-branch-chain):** PR 5a (macro + filtering + buffer) → PR 5b (meso) → PR 6 (micro + resource + error).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `observability.py` | New | 5 panels + filtering + buffer (~470 LOC) |
| `app.py` | Modified | `crew_event_bus` bridge + swap placeholder |

## Architectural Decisions

### 1. Reconnect Buffer Strategy
**Decision**: Time-based window (60s) instead of count-based (500 events)

**Rationale**: Event rate varies significantly between small and large crews. A fixed time window is more predictable for users — they get "last minute of activity" regardless of crew complexity. Implementation: filter deque by timestamp on replay rather than maxlen.

### 2. Empty State
**Decision**: Blank panel with history stub (no CTA)

**Rationale**: Non-intrusive. Users who know what they're doing don't need guidance. The history stub shows recent runs and provides a natural entry point without being pushy.

### 3. Retry Visibility
**Decision**: Show only explicit guardrail retries, not internal CrewAI tool failures

**Rationale**: Internal tool retries are noise. Guardrail retries are user-configured validation loops that the user explicitly cares about. Showing all retries would clutter the error panel.

### 4. Token Cost During Streaming
**Decision**: Show "—" until exact `resource.update` event arrives at completion

**Rationale**: Approximate costs during streaming are misleading and create false expectations. Users prefer accuracy over live updates for cost tracking. The resource panel updates once at task/crew completion.

## Risks

| Risk | Likely | Mitigation |
|------|--------|------------|
| >10k tokens degrades DOM | Med | `ui.markdown()` max-height; defer virtual scroll |
| Token cost inaccurate mid-stream | Med | Show "—" until resource.update; no approximation |
| Testing without real CrewAI | Med | Synthetic ProtocolEvent dicts |
| Multi-tab event leak | High | Mandatory crew_id check in every handler |

## Rollback Plan

Delete `observability.py`; revert `app.py` to placeholder. No data migration — observability is stateless.

## Dependencies

- `crew-engine` (BridgeListener, ProtocolEvent, CrewEngine.run on_event callback)
- `styles` (Token.THINKING, Token.ANSWER already defined)

## Success Criteria

- [ ] Tasks transition pending→running→completed in real-time
- [ ] Tool calls, delegations, memory ops appear as cards
- [ ] Tokens stream live with thinking/answer visual distinction
- [ ] Resource panel shows per-task tokens and total cost (—" until completion)
- [ ] Errors display with traceback; guardrail retries show attempt counters
- [ ] Two tabs running different crews see only their own events
- [ ] Reconnect within 60s replays missed events