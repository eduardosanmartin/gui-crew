# Tasks: Operations Playground

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 290–385 |
| 400-line budget risk | Medium |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Engine timeout + app route + playground skeleton | PR 1 | Base: feature/playground; includes empty state, accordion, controls |
| 2 | Execution flow + stacked panels + error handling + tests | PR 2 | Base: PR 1 branch; depends on PR 1 |

## Phase 1: Foundation

- [x] 1.1 `crew_engine.py`: Wrap `_test_agent_coro` `kickoff_async` in `asyncio.wait_for(timeout=max_execution_time)`; emit `agent.timeout` on `TimeoutError`
- [x] 1.2 `app.py`: Wire `/operations` route to `operations.render_operations` (lazy import) + add `/playground` route with nav entry

## Phase 2: Core Playground UI

- [x] 2.1 `operations.py`: Add `_playground_runs: deque(maxlen=2)` and run-state helpers
- [ ] 2.2 `operations.py`: Add `_render_playground_panel(run)` — header (role, timestamp, status badge, Stop), body via `_render_micro(crew_id)`, inline error label
- [ ] 2.3 `operations.py`: Add `_run_playground(role, prompt)` — generate `pg-{uuid}` crew_id, call `CrewEngine.test_agent(on_event=observability._dispatch)`, store handle in deque
- [ ] 2.4 `operations.py`: Add `_stop_playground(handle)` — set `flag["stop"] = True`, render "Execution stopped by user"
- [x] 2.5 `operations.py`: Add `_render_playground()` — agent dropdown, prompt textarea, Run/Stop buttons, empty state when no agents

## Phase 3: Integration

- [ ] 3.1 `operations.py`: Prepend "Playground" accordion (open) to `render_operations()`
- [ ] 3.2 `operations.py`: Wire Run button to `_run_playground()` and Stop to `_stop_playground()`
- [ ] 3.3 `operations.py`: Ensure error/timeout events render inline in panel without UI crash

## Phase 4: Testing

- [ ] 4.1 Unit test: `_test_agent_coro` timeout emits typed `agent.error` (mock sleep > timeout)
- [ ] 4.2 Unit test: `on_event` routes tokens to `observability._dispatch` and `_token_displays[crew_id]`
- [ ] 4.3 Unit test: `deque(maxlen=2)` evicts oldest on 3rd run
- [ ] 4.4 Unit test: `_stop_playground` sets `handle.flag["stop"] = True`
- [ ] 4.5 Unit test: Empty state disables Run and shows "No agents available"
- [ ] 4.6 Integration test: Full `test_agent` → synthetic tokens → verify `_token_displays[crew_id]` updated
