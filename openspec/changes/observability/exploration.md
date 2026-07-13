# Exploration: Observability Design — Open Questions Resolution

## Question 1: Guardrail Retry Detection

### Current State

`_CREWAI_EVENT_MAP` (crew_engine.py, lines 826–932) maps 14 CrewAI event types to
protocol dicts. None of the mapped events are guardrail-specific. The design doc
(design.md lines 103–104) notes this gap: _"BridgeListener._CREWAI_EVENT_MAP lacks
explicit guardrail retry events."_

The project's `models.py` already supports guardrails in `TaskModel`:
- `guardrails: list[str]` (line 134)
- `guardrail_max_retries: int = 3` (line 135)
- These are passed to CrewAI `Task` kwargs in `crew_engine.py` lines 737–740.

### CrewAI Event Types Available (from docs)

CrewAI emits these guardrail-specific events (documented in v1.15.0+/edge):

| Event Class | When Emitted |
|---|---|
| `LLMGuardrailStartedEvent` | Guardrail validation begins |
| `LLMGuardrailCompletedEvent` | Validation finished (success/failure) |
| `LLMGuardrailFailedEvent` | Validation failed (with error message) |
| `TaskFailedEvent` | Task failed (general, including guardrail failures) |

All are importable from `crewai.events`, same as existing mapped events.

### Approaches

1. **Direct Guardrail Events** — Add `LLMGuardrailFailedEvent`, `LLMGuardrailStartedEvent`,
   `LLMGuardrailCompletedEvent`, and `TaskFailedEvent` to `_CREWAI_EVENT_MAP`.

   - **Pros**: Explicit detection; no inference logic; captures guardrail-specific metadata
     (error message, attempt context). Matches CrewAI's documented API.
   - **Cons**: Requires adding 4 new entries (~40 lines) to `_CREWAI_EVENT_MAP`. Must
     verify exact field names on `LLMGuardrailFailedEvent` (error, guardrail_name).
   - **Effort**: Low

2. **State-Transition Inference** — Track `task.state_change` events in `observability.py`.
   Detect `running → failed → running` transitions for the same task name as retry.

   - **Pros**: Zero changes to `crew_engine.py`. Works with current event map.
   - **Cons**: Cannot distinguish guardrail retries from internal CrewAI retries (tool failures,
     LLM timeouts). Violates design decision #3 (proposal.md line 60: _"Show only explicit
     guardrail retries"_). Fragile — duplicates CrewAI's internal state machine.
   - **Effort**: Medium

3. **Hybrid** — Use guardrail events for explicit detection + state-change inference
   as fallback for CrewAI versions that lack these events.

   - **Pros**: Maximum compatibility across CrewAI versions.
   - **Cons**: More code; inference logic still fragile.
   - **Effort**: Medium

### Recommendation

**Approach 1** — Add guardrail events to `_CREWAI_EVENT_MAP`. This is the cleanest
implementation that matches the existing pattern. The `_CREWAI_EVENT_MAP` already
follows a consistent template; adding 4 more entries is straightforward.

New entries (protocol types):
- `LLMGuardrailStartedEvent` → `"type": "guardrail.started"`
- `LLMGuardrailCompletedEvent` → `"type": "guardrail.completed"`
- `LLMGuardrailFailedEvent` → `"type": "guardrail.failed"`
- `TaskFailedEvent` → `"type": "task.failed"`

### Risk

- Exact field names on `LLMGuardrailFailedEvent` (e.g., `error_message` vs `error` vs
  `reason`) can only be confirmed by reading the installed CrewAI source. Since CrewAI is
  not installed in this environment, extract with defensive `getattr(event, attr, default)`.
  If fields change between CrewAI versions, the extract lambda falls back gracefully.

---

## Question 2: Per-Task Resource Tracking

### Current State

`resource.update` is emitted ONCE at crew completion (crew_engine.py lines 1298–1308):
```python
on_event({
    "type": "resource.update",
    "task": crew_model.name,     # ← crew-level, not task-level
    "agent": "all",              # ← aggregate
    "tokens_in": input_toks,
    "tokens_out": output_toks,
    "cost": cost,
})
```

This is straight from `result.token_usage` (CrewAI's aggregate token counter).
`LLMCallCompletedEvent` (lines 902–906) only extracts `model` — it ignores
`agent_role`, `task_name`, and token counts that the event likely carries.

The observability spec (crew-observability/spec.md line 78) requires:
> _"a table with columns: Task, Agent, Duration, Tokens In, Tokens Out, Est. Cost, Iterations"_

### Data Available from CrewAI

| Source | What It Has | When Available |
|---|---|---|
| `LLMCallCompletedEvent` (event) | Per-call tokens, model, agent context | Streaming (during execution) |
| `LLMCallStartedEvent` (event) | Model, agent context | Streaming |
| `CrewOutput.token_usage` | Aggregate tokens only | Post-kickoff |
| `CrewOutput.tasks_output` | Per-task output objects | Post-kickoff |
| `crew.usage_metrics` | Per-task breakdown (v1.15.0+) | Post-kickoff |

### Approaches

1. **Enrich `LLMCallCompletedEvent` Extraction + Accumulate** — Extract `agent_role`,
   `task_name`, `tokens_in`, `tokens_out`, `response_time` from `LLMCallCompletedEvent`.
   Accumulate per-task in `observability.py._resources[crew_id]`.

   - **Pros**: Real-time per-task accumulation during execution. Resource panel updates
     live as LLM calls complete. No dependency on post-hoc parsing.
   - **Cons**: Requires knowing `LLMCallCompletedEvent`'s exact field names. If the
     event doesn't carry `task_name`, the mapping from agent → task requires tracking
     state transitions. If it doesn't carry input/output tokens, we can't accumulate.
   - **Effort**: Medium

2. **Post-hoc from `CrewOutput`** — After `crew.completed`, iterate
   `result.tasks_output` or `crew.usage_metrics` and emit per-task `resource.update`
   events in `_kickoff`.

   - **Pros**: Guaranteed correct — CrewAI computes the breakdown. No guessing about
     event fields.
   - **Cons**: Resource panel only populates AFTER crew completes. No live per-task
     tracking during execution. Violates spec expectation of live resource updates.
   - **Effort**: Low

3. **Custom `BridgeListener` Events** — Track task boundaries via
   `TaskStartedEvent`/`TaskCompletedEvent` in `BridgeListener` and emit custom
   `resource.task_update` events with accumulated LLM call data between boundaries.

   - **Pros**: Full control over data shape. Clean per-task bracketing.
   - **Cons**: Requires new event type in `BridgeListener` that's NOT in
     `_CREWAI_EVENT_MAP` (it's a synthetic event emitted from the listener itself).
     More code.
   - **Effort**: Medium-High

### Recommendation

**Approach 1 + 2 hybrid**: First, enrich `LLMCallCompletedEvent` extraction to capture
all available fields (`agent_role`, `task_name`, token counts, response_time). Then in
`observability.py`, accumulate per-task from live LLM events. On `crew.completed`,
cross-validate against `result.token_usage` totals.

If `LLMCallCompletedEvent` does NOT carry `task_name` or token counts, fall back to
**Approach 2**: emit two `resource.update` events from `_kickoff` — one per-task by
iterating `result.tasks_output` (or `crew.usage_metrics`) and one aggregate.

Implementation in `_kickoff` (lines 1298–1308):
```python
# After crew.completed, emit per-task resources
for task_output in getattr(result, "tasks_output", []) or []:
    on_event({
        "type": "resource.update",
        "crew_id": crew_id,
        "task": getattr(task_output, "description", "")[:60],
        "agent": getattr(getattr(task_output, "agent", None), "role", ""),
        "tokens_in": getattr(task_output, "tokens_in", 0),
        "tokens_out": getattr(task_output, "tokens_out", 0),
        "cost": ...,  # from pricing
        "ts": time.time(),
    })
```

### Risk

- `LLMCallCompletedEvent` field availability is unconfirmed. If the event carries only
  `model` (as the minimal extraction suggests), Approach 1 fails and we fall to Approach 2.
- `crew.usage_metrics` per-task breakdown availability depends on CrewAI version.
  The field exists in v1.15.0+ but structure varies.

---

## Question 3: NiceGUI Event Bus API

### Installed Version

**NiceGUI 3.14.0** — installed in `.venv`.

### Exact API

Located at `nicegui/event.py` (157 lines). The class is `Event[P]` (NOT `Event[T]`),
where `P` is a `ParamSpec`.

```python
from nicegui.event import Event

# Construction — no type parameter needed at runtime
crew_event_bus = Event()

# Subscribe — callback receives emitted args
crew_event_bus.subscribe(callback, expect_args=True)

# Unsubscribe
crew_event_bus.unsubscribe(callback)

# Emit — fire and forget (non-blocking)
crew_event_bus.emit(payload)

# Call — async, awaits all subscribers
await crew_event_bus.call(payload)

# Emitted — async, returns first emitted value
result = await crew_event_bus.emitted(timeout=5.0)
```

Key details:
- `Event()` constructor takes no arguments (line 44)
- `subscribe()` has keyword-only params: `expect_args` (default `None` = auto-detect) and
  `unsubscribe_on_delete` (line 58–96)
- `emit()` is synchronous fire-and-forget — does NOT await subscribers (line 105–108)
- Subscribers auto-unsubscribe when the calling UI client is deleted (prevents memory
  leaks from short-lived UI contexts)
- `call()` awaits all subscribers asynchronously (line 110–112)
- `emitted()` returns a future that resolves on next emit (line 114–131)

### For Our Use Case

The design doc's proposed interface works as-is:
```python
# observability.py
from nicegui.event import Event
from crew_engine import ProtocolEvent

crew_event_bus: Event = Event()  # Module singleton

# app.py bridge
import observability
crew_event_bus = observability.crew_event_bus
# engine.run(model, inputs, on_event=lambda e: crew_event_bus.emit(e))
```

Since `emit()` is fire-and-forget and subscribers auto-unsubscribe on UI client
deletion, this handles the multi-tab scenario correctly out of the box.

### Recommendation

Use `nicegui.event.Event` directly. No custom pub/sub wrapper needed. The `Event`
class added in NiceGUI 3.0.0 was built exactly for this use case — distributing
information from long-lived objects (data models) to short-lived UI contexts.

---

## Summary of Recommendations

| Question | Recommendation | Effort | Changes Required |
|---|---|---|---|
| Q1: Guardrail retries | Add `LLMGuardrailFailedEvent`, `LLMGuardrailStartedEvent`, `LLMGuardrailCompletedEvent`, `TaskFailedEvent` to `_CREWAI_EVENT_MAP` | Low | `crew_engine.py`: ~40 lines in `_CREWAI_EVENT_MAP` |
| Q2: Per-task resources | Enrich `LLMCallCompletedEvent` extraction; fallback: post-hoc per-task from `CrewOutput.tasks_output` | Medium | `crew_engine.py`: enrich extract lambda; `_kickoff`: per-task loop |
| Q3: Event bus API | Use `nicegui.event.Event` directly | Low | `observability.py`: 1 import; `app.py`: 3-line bridge |

## Affected Areas

- `crew_engine.py` — `_CREWAI_EVENT_MAP` (lines 826–932): add 4 event entries + enrich
  `LLMCallCompletedEvent` extract lambda
- `crew_engine.py` — `_kickoff` (lines 1298–1308): add per-task `resource.update` loop
- `observability.py` — new file: consume guardrail events, accumulate per-task resources
- `app.py` — lines 169–172: swap placeholder for `observability.render_observability`

## Risks

- **CrewAI not installed**: Cannot verify `LLMGuardrailFailedEvent` and
  `LLMCallCompletedEvent` field names. Extract lambdas use `getattr` with defaults —
  fields that don't exist will silently default to `""`/`0`, which is acceptable at
  runtime but may hide missing data. Mitigation: add integration test with real CrewAI
  before merging.
- **CrewAI version variance**: Event classes and field names may differ between CrewAI
  1.14.x and 1.15.x. The `_discover_event_classes` method (line 1006–1018) already
  handles missing classes gracefully (skips if `getattr` returns `None`).
- **Per-task token accuracy**: If `LLMCallCompletedEvent` doesn't carry token counts,
  per-task resource tracking degrades to post-hoc only — no live updates during execution.

## Design.md Updates Required

Update `openspec/changes/observability/design.md`:
1. **Line 104**: Replace _"Need to determine if CrewAI emits distinguishable events"_
   → _"CrewAI emits `LLMGuardrailFailedEvent`, `LLMGuardrailStartedEvent`,
   `LLMGuardrailCompletedEvent`, and `TaskFailedEvent` — add to `_CREWAI_EVENT_MAP`."_
2. **Line 105**: Replace _"May need per-task resource.update events"_
   → _"Enrich `LLMCallCompletedEvent` extraction with agent/task/token context; fallback to
   post-hoc per-task breakdown from `CrewOutput.tasks_output` in `_kickoff`."_
3. **Line 106**: Replace _"Confirm exact event-bus API"_
   → _"Confirmed: `nicegui.event.Event` (v3.14.0) — `Event()` constructor,
   `subscribe(cb, expect_args=True)`, `emit(payload)`. No custom wrapper needed."_
