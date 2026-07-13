# Tasks: Observability — Real-Time Execution Dashboard

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~750 (470 + 40 + 30 + 200 tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 5a → PR 5b → PR 6 |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Macro + infrastructure + bridge | PR 5a | Targets `feature/observability`; includes `crew_engine.py` events, `app.py` bridge |
| 2 | Meso layer | PR 5b | Targets PR 5a branch |
| 3 | Micro + Resource + Error | PR 6 | Targets PR 5b branch |

## Phase 1: Foundation

- [x] 1.1 Add `LLMGuardrailStartedEvent`, `LLMGuardrailFailedEvent`, `LLMGuardrailCompletedEvent`, `TaskFailedEvent` to `_CREWAI_EVENT_MAP` in `crew_engine.py`
- [x] 1.2 Create `observability.py` with `crew_event_bus` singleton and module-level state dicts (`_crew_state`, `_activity_log`, `_token_elements`, `_resources`, `_errors`, `_event_buffer`)
- [x] 1.3 Implement `_buffer_event` append and `_replay_buffer` with 60 s timestamp eviction on insert

## Phase 2: PR 5a — Macro + Filtering + Bridge

- [x] 2.1 Implement `_dispatch` with mandatory `crew_id` gate and type routing to handlers
- [x] 2.2 Implement `_render_macro` with pipeline task states (pending/running/completed/failed/retrying), progress bar, and status bar
- [x] 2.3 Implement empty state: blank panel with history stub when no active `crew_id`
- [x] 2.4 Wire `app.py` bridge: import `observability`, expose `crew_event_bus`, swap `_render_observability_placeholder` → `observability.render_observability`

## Phase 3: PR 5b — Meso Layer

- [x] 3.1 Implement `_render_meso` with agent cards, tool invocation cards (name, input, result, duration, errors), and delegation cards (from/to, context, response)
- [x] 3.2 Add knowledge operation cards to meso layer (read/write with type)

## Phase 4: PR 6 — Micro + Resource + Error

- [x] 4.1 Implement `_render_micro` with direct DOM mutation for token streaming; apply `Token.THINKING` (italic/dimmed) vs `Token.ANSWER` styles; show tokens/sec and cumulative count
- [x] 4.2 Implement `_render_resource` with per-task table (tokens in+out, cost, duration, iterations); show "—" until `resource.update` arrives
- [x] 4.3 Implement `_render_error` with traceback display; filter to guardrail retries only; show attempt counters

## Phase 5: Testing

- [x] 5.1 Write `tests/test_observability.py`: test crew_id filtering gate with synthetic ProtocolEvents (mismatched `crew_id` → zero state mutation)
- [x] 5.2 Test buffer eviction: inject events with controlled `ts`; assert >60 s evicted and replay order is chronological
- [x] 5.3 Test state accumulation, token style selection, and guardrail retry counter; mock `crew_event_bus` subscribers
- [ ] 5.4 Test `app.py` bridge wiring: mock `CrewEngine.run`; assert events reach `crew_event_bus` and `_dispatch`

## Phase 6: Cleanup

- [ ] 6.1 Remove `_render_observability_placeholder` from `app.py`
- [ ] 6.2 Add module docstring and inline comments to `observability.py`
