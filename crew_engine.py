"""CrewAI integration layer for gui-crew — adapter, event bridge, and execution.

This module is the **sole** importer of the ``crewai`` package in the project
(adapter isolation principle).  UI code imports ``crew_engine`` for the event
protocol and execution handle but never reaches CrewAI types directly.

Public surface
--------------
* ``Adapter`` — converts Pydantic models ↔ CrewAI instances.
* ``CallbackRouter`` — expands builder callback template IDs to Python callables.
* ``BridgeListener`` — registers on ``crewai_event_bus``, translates CrewAI
  events → flat JSON protocol dicts with ``crew_id``.
* ``CrewEngine`` — spawns background-thread executions, exposes ``run`` /
  ``stop`` / ``test_agent``.
* ``ProgressToolWrapper`` — wraps CrewAI tools for progress events + cooperative
  cancellation.
* ``ExecutionHandle`` — thread + flag + listener that ``run()`` returns.
* ``load_pricing`` / ``calculate_cost`` — pricing helpers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# CrewAI adapter boundary (THE ONLY place that imports crewai in this project)
# ---------------------------------------------------------------------------

_crewai_module: Any = None
_CrewAIAgent: type | Any = None
_CrewAICrew: type | Any = None
_CrewAILLM: type | Any = None
_CrewAITask: type | Any = None
_crewai_event_bus: Any = None
_BaseEventListener: type | Any = None
_CrewAIBaseTool: type | Any = None
_CrewAIMemory: type | Any = None
_CrewAIBaseLLM: type | Any = None

try:
    import crewai as _crewai_module

    from crewai import Agent as _CrewAIAgent
    from crewai import Crew as _CrewAICrew
    from crewai import LLM as _CrewAILLM
    from crewai import Task as _CrewAITask
    from crewai.events.event_bus import crewai_event_bus as _crewai_event_bus
    from crewai.events.event_listener import BaseEventListener as _BaseEventListener
    from crewai.tools import BaseTool as _CrewAIBaseTool
except ImportError:
    # Graceful degradation for environments without CrewAI (e.g. CI/mocks).
    # The Adapter will raise a descriptive error at call time.
    pass

import models  # project Pydantic layer
import pydantic

# Non-None base for ProgressToolWrapper (handles CrewAI-not-installed case)
_ProgressToolBase = _CrewAIBaseTool if _CrewAIBaseTool is not None else object

# ═══════════════════════════════════════════════════════════════════════════
#  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

#: A flat JSON-serialisable dict sent via the WebSocket event protocol.
ProtocolEvent = dict[str, Any]

#: Callback signature for ``CrewEngine.run(on_event=...)``.
EventCallback = Callable[[ProtocolEvent], None]

#: Cooperative-cancellation flag shared across threads.
StopFlag = dict[str, bool]


# ═══════════════════════════════════════════════════════════════════════════
#  ExecutionHandle
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionHandle:
    """Handle returned by :meth:`CrewEngine.run` to manage a running crew.

    Attributes
    ----------
    thread : threading.Thread
        The background worker thread that executes ``kickoff_async``.
    flag : StopFlag
        Mutable dict ``{"stop": bool}`` set to ``True`` to request
        cancellation.
    listener : BridgeListener
        The registered event-listener (can be used to verify events).
    crew_id : str
        Unique identifier for this execution (used for multi-tab filtering).
    """

    thread: threading.Thread
    flag: StopFlag
    listener: BridgeListener  # type: ignore[name-defined]  # forward ref
    crew_id: str


# ═══════════════════════════════════════════════════════════════════════════
#  Pricing helpers
# ═══════════════════════════════════════════════════════════════════════════

_PRICES: dict[str, dict[str, float]] | None = None


def _default_pricing_path() -> Path:
    """Return the absolute path to ``pricing.yaml`` next to this file."""
    return Path(__file__).resolve().parent / "pricing.yaml"


def load_pricing(path: str | Path | None = None) -> dict[str, dict[str, float]]:
    """Load per-model pricing from a YAML file (cached).

    Parameters
    ----------
    path : str | Path | None
        Path to ``pricing.yaml``; defaults to the file next to
        ``crew_engine.py``.

    Returns
    -------
    dict
        Mapping ``model_name → {"input_per_1k": float,
        "output_per_1k": float}``.
    """
    global _PRICES

    target = Path(path) if path else _default_pricing_path()

    try:
        import yaml
    except ImportError:
        return {}

    try:
        with open(target, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}

    prices: dict[str, dict[str, float]] = {}
    for model, entry in raw.items():
        if isinstance(entry, dict):
            prices[str(model)] = {
                "input_per_1k": float(entry.get("input_per_1k", 0)),
                "output_per_1k": float(entry.get("output_per_1k", 0)),
            }
    return prices


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Calculate estimated cost in USD.

    Parameters
    ----------
    model : str
        Model identifier (e.g. ``"openai/gpt-4o"``).
    input_tokens : int
        Number of input/prompt tokens.
    output_tokens : int
        Number of output/completion tokens.

    Returns
    -------
    float | None
        Estimated cost in USD, or ``None`` when the model is unknown.
    """
    prices = load_pricing()

    # Try exact match first, then partial (model name may be "openai/gpt-4o"
    # while pricing key is "gpt-4o").
    entry = prices.get(model) or prices.get(model.split("/")[-1])

    if entry is None:
        return None

    cost_input = (input_tokens / 1000.0) * entry["input_per_1k"]
    cost_output = (output_tokens / 1000.0) * entry["output_per_1k"]
    return round(cost_input + cost_output, 6)


# ═══════════════════════════════════════════════════════════════════════════
#  CancelledError
# ═══════════════════════════════════════════════════════════════════════════

class CancelledError(Exception):
    """Raised when execution is cancelled via the stop flag."""


# ═══════════════════════════════════════════════════════════════════════════
#  ProgressToolWrapper
# ═══════════════════════════════════════════════════════════════════════════

class ProgressToolWrapper(_ProgressToolBase):
    """Wraps a CrewAI tool to emit ``tool.progress`` events and support
    cooperative cancellation.

    The wrapper delegates to the inner tool's ``_run`` method while checking
    ``flag["stop"]`` on each progress pulse.

    .. note::

       Cancellation is **cooperative** — the inner tool's call runs in a
       background thread and is polled every ~1 s.  ``future.cancel()`` does
       **not** stop an actively running Python call (it returns ``False`` for
       running futures).  The tool must reach a yield / return point on its
       own; no sub-second responsiveness is guaranteed.

    Parameters
    ----------
    tool : Any
        The CrewAI tool instance to wrap (must have ``name``, ``_run``).
    flag : StopFlag
        Shared cancellation dict.
    on_event : EventCallback
        Callback invoked with ``tool.progress`` protocol events.
    crew_id : str
        Unique execution identifier.
    """

    def __init__(
        self,
        tool: Any,
        flag: StopFlag,
        on_event: EventCallback,
        crew_id: str,
    ) -> None:
        # Safely extract name/description (MagicMock returns non-string
        # for missing attributes, which would fail Pydantic validation).
        _raw_name = getattr(tool, "name", None)
        name = _raw_name if isinstance(_raw_name, str) else "UnknownTool"
        _raw_desc = getattr(tool, "description", None)
        description = _raw_desc if isinstance(_raw_desc, str) else ""
        if _CrewAIBaseTool is not None:
            super().__init__(name=name, description=description)
        else:
            super().__init__()
        self._inner_tool = tool
        self._flag = flag
        self._on_event = on_event
        self._crew_id = crew_id

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the inner tool."""
        return getattr(self._inner_tool, name)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the inner tool with progress pulses.

        Cancellation is cooperative — the inner tool runs in a separate
        thread and is checked every ~1 s.  ``future.cancel()`` does **not**
        abort an already-running Python call; it returns ``False`` for
        running futures.  The tool must reach a yield / return point.
        """
        start_ts = time.monotonic()
        tool_name = self.name
        last_pulse = start_ts

        def _pulse(elapsed: float) -> None:
            try:
                self._on_event({
                    "type": "tool.progress",
                    "crew_id": self._crew_id,
                    "tool_name": tool_name,
                    "elapsed_ms": int(elapsed * 1000),
                    "status_message": f"Running for {elapsed:.0f}s...",
                    "ts": time.time(),
                })
            except Exception:
                pass

        # Start the inner tool in a background thread so we can pulse
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._inner_tool._run, *args, **kwargs)
            while not future.done():
                if self._flag.get("stop"):
                    # NOTE: future.cancel() returns False for running
                    # futures — the underlying call is NOT stopped.
                    future.cancel()
                    raise CancelledError("Tool cancelled by user")
                elapsed = time.monotonic() - start_ts
                if time.monotonic() - last_pulse >= 5.0:
                    _pulse(elapsed)
                    last_pulse = time.monotonic()
                try:
                    future.result(timeout=1)
                except concurrent.futures.TimeoutError:
                    pass

            # Future is done — get result (re-raises if tool raised)
            results = future.result()

        # Final pulse
        elapsed = time.monotonic() - start_ts
        _pulse(elapsed)

        return results


# ═══════════════════════════════════════════════════════════════════════════
#  JSON Schema → Pydantic model (for output_json)
# ═══════════════════════════════════════════════════════════════════════════

def _json_schema_to_pydantic_model(schema: dict) -> type:
    """Convert a JSON Schema dict to a dynamic Pydantic model class.

    This is used to convert ``TaskModel.output_json_schema`` (a ``dict``)
    into the ``type[BaseModel]`` that CrewAI's ``Task`` expects for
    ``output_json``.
    """
    from typing import Optional

    _TYPE_MAP: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    fields: dict[str, tuple] = {}
    for field_name, field_schema in properties.items():
        json_type = field_schema.get("type", "string")
        py_type = _TYPE_MAP.get(json_type, str)
        if field_name in required_fields:
            fields[field_name] = (py_type, ...)
        else:
            fields[field_name] = (Optional[py_type], None)

    model_name = schema.get("title", "OutputModel")
    return pydantic.create_model(model_name, **fields)  # type: ignore[no-untyped-call]


# ═══════════════════════════════════════════════════════════════════════════
#  CallbackRouter  —  template IDs → Python callables
# ═══════════════════════════════════════════════════════════════════════════

class CallbackRouter:
    """Expand builder callback template IDs to actual Python callables.

    The builder stores callbacks as template IDs (e.g. ``"log_to_file"``)
    so that they survive serialization.  This class expands those IDs to
    real ``Callable`` objects with error-safe wrappers before they are
    passed to CrewAI.

    Every wrapper catches exceptions and logs them — callbacks MUST NOT
    break crew execution.

    Built-in templates
    ------------------
    * ``log_to_file`` — appends callback invocation details to a file.
    * ``print_to_console`` — prints invocation details to stdout.
    * ``send_webhook`` — stub that logs what *would* be sent (no actual HTTP).
    """

    _LOG = logging.getLogger(__name__)

    # ------------------------------------------------------------------ #
    #  Template factories — each returns a callable (static methods so
    #  they can be stored directly in the registry).
    # ------------------------------------------------------------------ #

    @staticmethod
    def _log_to_file(config: dict | None = None) -> Callable[..., None]:
        config = config or {}
        filepath = config.get("filepath", "crew_callback.log")

        # Path traversal protection: validate the path
        from pathlib import Path
        try:
            path = Path(filepath)
            # Only validate relative paths - absolute paths are explicitly provided by user
            if not path.is_absolute():
                resolved = path.resolve()
                cwd = Path.cwd().resolve()
                # Block relative paths that escape CWD
                if not str(resolved).startswith(str(cwd)):
                    CallbackRouter._LOG.warning(
                        "log_to_file: filepath '%s' resolves outside safe directory, "
                        "using default 'crew_callback.log'", filepath
                    )
                    filepath = "crew_callback.log"
        except Exception:
            CallbackRouter._LOG.warning(
                "log_to_file: invalid filepath '%s', using default", filepath
            )
            filepath = "crew_callback.log"

        def callback(*args: Any, **kwargs: Any) -> None:
            try:
                with open(filepath, "a", encoding="utf-8") as fh:
                    fh.write(f"[{time.time()}] Callback invoked: "
                             f"args={args}, kwargs={kwargs}\n")
            except Exception:
                CallbackRouter._LOG.exception(
                    "log_to_file callback failed"
                )

        return callback

    @staticmethod
    def _print_to_console(config: dict | None = None) -> Callable[..., None]:
        config = config or {}

        def callback(*args: Any, **kwargs: Any) -> None:
            try:
                print(f"[Callback] {args} {kwargs}")
            except Exception:
                CallbackRouter._LOG.exception(
                    "print_to_console callback failed"
                )

        return callback

    @staticmethod
    def _send_webhook(config: dict | None = None) -> Callable[..., None]:
        config = config or {}
        url = config.get("url", "")

        def callback(*args: Any, **kwargs: Any) -> None:
            try:
                CallbackRouter._LOG.info(
                    "send_webhook stub: would POST to %s with %s",
                    url,
                    kwargs,
                )
            except Exception:
                CallbackRouter._LOG.exception(
                    "send_webhook callback failed"
                )

        return callback

    #: Template ID → factory callable (returns error-safe callable).
    _TEMPLATES: dict[str, Callable[..., Callable[..., None]]] = {
        "log_to_file": _log_to_file,
        "print_to_console": _print_to_console,
        "send_webhook": _send_webhook,
    }

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    @classmethod
    def expand_callbacks(
        cls,
        callbacks: dict[str, Any],
    ) -> dict[str, list[Callable[..., Any]]]:
        """Expand template IDs to Python callables for CrewAI.

        Parameters
        ----------
        callbacks : dict
            Mapping of callback type names (e.g. ``"before_kickoff"``)
            to template IDs or config dicts.  Each value may be a single
            string / dict or a list of either.

        Returns
        -------
        dict[str, list[Callable]]
            Dictionary with the same keys, where each value is a list of
            error-safe callables ready for CrewAI.
        """
        result: dict[str, list[Callable[..., Any]]] = {}

        for callback_type, callback_specs in callbacks.items():
            # Normalise to list
            if not isinstance(callback_specs, list):
                specs: list[Any] = [callback_specs]
            else:
                specs = callback_specs

            callables: list[Callable[..., Any]] = []
            for spec in specs:
                # Resolve template ID and optional config
                if isinstance(spec, str):
                    template_id: str = spec
                    config: dict[str, Any] = {}
                elif isinstance(spec, dict):
                    template_id = spec.get("template", "")
                    config = spec.get("config", {})
                else:
                    cls._LOG.warning(
                        "Skipping unsupported callback spec type: %s", type(spec)
                    )
                    continue

                # Validate template_id is a string (unhashable types like lists would crash .get())
                if not isinstance(template_id, str):
                    cls._LOG.warning(
                        "Skipping callback with non-string template_id: %s (type: %s)",
                        template_id, type(template_id).__name__
                    )
                    continue

                factory = cls._TEMPLATES.get(template_id)
                if factory is not None:
                    try:
                        callables.append(factory(config))
                    except Exception:
                        cls._LOG.exception(
                            "Failed to create callback '%s'", template_id
                        )
                else:
                    cls._LOG.warning(
                        "Unknown callback template: %s", template_id
                    )

            if callables:
                result[callback_type] = callables

        return result


# ═══════════════════════════════════════════════════════════════════════════
#  Adapter  —  Pydantic models ↔ CrewAI instances
# ═══════════════════════════════════════════════════════════════════════════

class Adapter:
    """Convert Pydantic gui-crew models to native CrewAI instances.

    This is the **sole** class in the project that constructs CrewAI objects.
    When CrewAI evolves, only this file (and potentially the Pydantic models)
    need updating.
    """

    _LOG = logging.getLogger(__name__)

    @staticmethod
    def _build_llm(llm_model: models.LLMModel | None) -> Any | None:
        """Convert an :class:`models.LLMModel` to a ``crewai.LLM``."""
        if llm_model is None or _CrewAILLM is None:
            return None
        kwargs = llm_model.model_dump(exclude_defaults=False)
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return _CrewAILLM(**clean)

    @staticmethod
    def _resolve_tool(
        tool_ref: models.ToolRef,
        flag: StopFlag | None = None,
        on_event: EventCallback | None = None,
        crew_id: str = "",
    ) -> Any:
        """Resolve a :class:`models.ToolRef` to a CrewAI tool instance.

        When *flag* / *on_event* / *crew_id* are provided the resulting tool
        is wrapped in :class:`ProgressToolWrapper`.
        """
        tool: Any = None

        if tool_ref.kind == "builtin":
            tool = Adapter._resolve_builtin_tool(tool_ref)
        else:
            tool = Adapter._build_custom_tool(tool_ref)

        # Wrap for progress events when applicable
        if tool is not None and flag is not None and on_event is not None:
            tool = ProgressToolWrapper(
                tool=tool,
                flag=flag,
                on_event=on_event,
                crew_id=crew_id,
            )

        return tool

    @staticmethod
    def _resolve_builtin_tool(tool_ref: models.ToolRef) -> Any:
        """Resolve a built-in tool by name via ``crewai_tools``."""
        try:
            import crewai_tools  # type: ignore[import-untyped]
        except ImportError:
            return {
                "name": tool_ref.name,
                "kind": "builtin",
                "params": tool_ref.params,
            }

        tool_cls = getattr(crewai_tools, tool_ref.name, None)
        if tool_cls is None:
            return {
                "name": tool_ref.name,
                "kind": "builtin",
                "params": tool_ref.params,
            }
        try:
            return tool_cls(**tool_ref.params)
        except Exception:
            return tool_cls()

    @staticmethod
    def _build_custom_tool(tool_ref: models.ToolRef) -> Any:
        """Build a custom tool placeholder from ``ToolRef``."""
        return {
            "name": tool_ref.name,
            "kind": "custom",
            "params": tool_ref.params,
            "args_schema": tool_ref.args_schema,
        }

    @staticmethod
    def build_crewai_object(
        crew_model: models.CrewModel,
        flag: StopFlag | None = None,
        on_event: EventCallback | None = None,
        crew_id: str = "",
    ) -> Any:
        """Convert a full :class:`models.CrewModel` to a CrewAI ``Crew``.

        Parameters
        ----------
        crew_model : models.CrewModel
            Validated crew configuration.
        flag : StopFlag | None
            Cooperative-cancellation flag shared with :class:`CrewEngine`.
        on_event : EventCallback | None
            Callback for progress / error events.
        crew_id : str
            Unique execution identifier.

        Returns
        -------
        crewai.Crew
            Ready-to-run CrewAI crew instance.
        """
        if _CrewAIAgent is None or _CrewAICrew is None:
            raise ImportError(
                "CrewAI is required for the adapter. "
                "Install it with: pip install crewai>=1.15"
            )

        # -- Agents ---------------------------------------------------------
        crewai_agents: list[Any] = []
        for agent_model in crew_model.agents:
            agent_kwargs: dict[str, Any] = {
                "role": agent_model.role,
                "goal": agent_model.goal,
                "backstory": agent_model.backstory or "",
                "allow_delegation": agent_model.allow_delegation,
                "allow_code_execution": agent_model.allow_code_execution,
            }

            # LLM
            llm = Adapter._build_llm(agent_model.llm)
            if llm is not None:
                agent_kwargs["llm"] = llm

            # Function-calling LLM
            fcall_llm = Adapter._build_llm(agent_model.function_calling_llm)
            if fcall_llm is not None:
                agent_kwargs["function_calling_llm"] = fcall_llm

            # Tools (with progress wrapper)
            if agent_model.tools:
                agent_kwargs["tools"] = [
                    Adapter._resolve_tool(t, flag, on_event, crew_id)
                    for t in agent_model.tools
                ]

            # Memory (resolve MemoryConfig → bool)
            if isinstance(agent_model.memory, models.MemoryConfig):
                if agent_model.memory.enabled:
                    agent_kwargs["memory"] = True
            elif agent_model.memory:
                agent_kwargs["memory"] = True

            # Optional fields
            if agent_model.max_iter is not None:
                agent_kwargs["max_iter"] = agent_model.max_iter
            if agent_model.system_template:
                agent_kwargs["system_template"] = agent_model.system_template
            if agent_model.multimodal:
                agent_kwargs["multimodal"] = True

            # model_extra passthrough (e.g. verbose, cache, max_rpm, ...)
            if agent_model.model_extra:
                agent_kwargs.update(agent_model.model_extra)

            crewai_agents.append(_CrewAIAgent(**agent_kwargs))

        # -- Tasks ----------------------------------------------------------
        crewai_tasks: list[Any] = []
        for task_model in crew_model.tasks:
            task_kwargs: dict[str, Any] = {
                "description": task_model.description,
                "expected_output": task_model.expected_output,
            }

            # Agent reference
            if task_model.agent_role:
                matching = [
                    a for a in crewai_agents if a.role == task_model.agent_role
                ]
                if matching:
                    task_kwargs["agent"] = matching[0]

            # Tools
            if task_model.tools:
                task_kwargs["tools"] = [
                    Adapter._resolve_tool(t, flag, on_event, crew_id)
                    for t in task_model.tools
                ]

            # Optional fields
            if task_model.output_file:
                task_kwargs["output_file"] = task_model.output_file
            if task_model.output_json_schema:
                task_kwargs["output_json"] = _json_schema_to_pydantic_model(
                    task_model.output_json_schema
                )
            if task_model.human_input:
                task_kwargs["human_input"] = True
            if task_model.async_execution:
                task_kwargs["async_execution"] = True
            if task_model.guardrails:
                task_kwargs["guardrails"] = task_model.guardrails
            if task_model.guardrail_max_retries != 3:
                task_kwargs["guardrail_max_retries"] = task_model.guardrail_max_retries
            if task_model.markdown:
                task_kwargs["markdown"] = True

            if task_model.model_extra:
                task_kwargs.update(task_model.model_extra)

            crewai_task = _CrewAITask(**task_kwargs)
            crewai_tasks.append(crewai_task)

        # Resolve context (task name → task instance)
        name_map = {
            t_model.name: crewai_tasks[i]
            for i, t_model in enumerate(crew_model.tasks)
        }
        for i, task_model in enumerate(crew_model.tasks):
            ctask = crewai_tasks[i]
            resolved = []
            for ctx_name in task_model.context:
                if ctx_name in name_map and name_map[ctx_name] is not ctask:
                    resolved.append(name_map[ctx_name])
            if resolved:
                ctask.context = resolved  # type: ignore[attr-defined]

        # -- Crew -----------------------------------------------------------
        crew_kwargs: dict[str, Any] = {
            "name": crew_model.name,
            "description": crew_model.description or "",
            "agents": crewai_agents,
            "tasks": crewai_tasks,
            "process": crew_model.process,
            "verbose": crew_model.verbose,
        }

        if isinstance(crew_model.memory, models.MemoryConfig):
            if crew_model.memory.enabled:
                crew_kwargs["memory"] = True
        elif crew_model.memory:
            crew_kwargs["memory"] = True
        if crew_model.planning:
            crew_kwargs["planning"] = True
        if crew_model.manager_llm:
            crew_kwargs["manager_llm"] = Adapter._build_llm(crew_model.manager_llm)
        if crew_model.manager_agent_role:
            matching = [
                a for a in crewai_agents
                if a.role == crew_model.manager_agent_role
            ]
            if matching:
                crew_kwargs["manager_agent"] = matching[0]
        if crew_model.knowledge_sources:
            crew_kwargs["knowledge_sources"] = crew_model.knowledge_sources
        if crew_model.embedder:
            crew_kwargs["embedder"] = crew_model.embedder

        if crew_model.model_extra:
            crew_kwargs.update(crew_model.model_extra)

        # -- Callback expansion ----------------------------------------------
        if crew_model.callbacks:
            expanded = CallbackRouter.expand_callbacks(crew_model.callbacks)
            for cb_type, cb_list in expanded.items():
                # CrewAI natively supports these as lists
                if cb_type in ("before_kickoff", "after_kickoff"):
                    crew_kwargs[f"{cb_type}_callbacks"] = cb_list
                # CrewAI expects a single callable for these
                elif cb_type in ("step_callback", "task_callback"):
                    if cb_list:
                        if len(cb_list) > 1:
                            cls._LOG.warning(
                                "CrewAI expects a single callable for '%s'; "
                                "using first of %d callbacks",
                                cb_type, len(cb_list)
                            )
                        crew_kwargs[cb_type] = cb_list[0]
                # Pass through for future CrewAI callback types
                else:
                    crew_kwargs[f"{cb_type}_callbacks"] = cb_list

        return _CrewAICrew(**crew_kwargs)


# ═══════════════════════════════════════════════════════════════════════════
#  BridgeListener  —  CrewAI event bus → flat protocol dicts
# ═══════════════════════════════════════════════════════════════════════════

_CREWAI_EVENT_MAP: dict[str, dict[str, Any]] = {
    "CrewKickoffStartedEvent": {
        "type": "crew.started",
        "extract": lambda ev: {
            "crew_name": getattr(ev, "crew_name", ""),
            "task_count": getattr(ev, "task_count", 0),
        },
    },
    "CrewKickoffCompletedEvent": {
        "type": "crew.completed",
        "extract": lambda ev: {
            "status": "success",
            "token_usage": getattr(ev, "token_usage", None),
            "output": getattr(ev, "output", None),
        },
    },
    "TaskStartedEvent": {
        "type": "task.state_change",
        "extract": lambda ev: {
            "task_name": getattr(ev, "task_name", ""),
            "old_state": getattr(ev, "from_state", "pending"),
            "new_state": "running",
        },
    },
    "TaskCompletedEvent": {
        "type": "task.state_change",
        "extract": lambda ev: {
            "task_name": getattr(ev, "task_name", ""),
            "old_state": "running",
            "new_state": getattr(ev, "state", "completed"),
        },
    },
    "AgentExecutionStartedEvent": {
        "type": "agent.started",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_role", ""),
            "task_name": getattr(ev, "task_name", ""),
        },
    },
    "AgentExecutionCompletedEvent": {
        "type": "agent.completed",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_role", ""),
            "task_name": getattr(ev, "task_name", ""),
        },
    },
    "ToolUsageStartedEvent": {
        "type": "tool.call_start",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_role", ""),
            "tool_name": getattr(ev, "tool_name", ""),
            "params": getattr(ev, "tool_args", {}),
        },
    },
    "ToolUsageFinishedEvent": {
        "type": "tool.call_end",
        "extract": lambda ev: {
            "result_summary": getattr(ev, "output", ""),
            "duration_ms": getattr(ev, "duration", 0),
            "error": getattr(ev, "error", None),
        },
    },
    "LLMStreamChunkEvent": {
        "type": "token.stream",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_role", ""),
            "is_thinking": getattr(ev, "is_reasoning", False),
            "text": getattr(ev, "chunk", ""),
        },
    },
    "LLMCallStartedEvent": {
        "type": "llm.call_started",
        "extract": lambda ev: {
            "model": getattr(ev, "model", ""),
        },
    },
    "LLMCallCompletedEvent": {
        "type": "llm.call_completed",
        "extract": lambda ev: {
            "model": getattr(ev, "model", ""),
        },
    },
    "MemoryQueryCompletedEvent": {
        "type": "memory.op",
        "extract": lambda ev: {
            "kind": getattr(ev, "kind", "query"),
            "query": getattr(ev, "query", ""),
            "query_time_ms": getattr(ev, "query_time_ms", 0),
        },
    },
    "KnowledgeRetrievalStartedEvent": {
        "type": "knowledge.op",
        "extract": lambda ev: {
            "kind": "retrieval_started",
            "query": getattr(ev, "query", ""),
            "chunks": getattr(ev, "chunks", 0),
        },
    },
    "KnowledgeRetrievalCompletedEvent": {
        "type": "knowledge.op",
        "extract": lambda ev: {
            "kind": "retrieval_completed",
            "query": getattr(ev, "query", ""),
            "chunks": getattr(ev, "chunks", 0),
        },
    },
    "LLMGuardrailStartedEvent": {
        "type": "guardrail.started",
        "extract": lambda ev: {
            "guardrail_name": getattr(ev, "guardrail_name", ""),
            "task_name": getattr(ev, "task_name", ""),
            "attempt": getattr(ev, "attempt", 1),
        },
    },
    "LLMGuardrailCompletedEvent": {
        "type": "guardrail.completed",
        "extract": lambda ev: {
            "guardrail_name": getattr(ev, "guardrail_name", ""),
            "task_name": getattr(ev, "task_name", ""),
            "output": getattr(ev, "output", None),
        },
    },
    "LLMGuardrailFailedEvent": {
        "type": "guardrail.failed",
        "extract": lambda ev: {
            "guardrail_name": getattr(ev, "guardrail_name", ""),
            "task_name": getattr(ev, "task_name", ""),
            "error": getattr(ev, "error", ""),
            "attempt": getattr(ev, "attempt", 1),
        },
    },
    "TaskFailedEvent": {
        "type": "task.failed",
        "extract": lambda ev: {
            "task_name": getattr(ev, "task_name", ""),
            "error": getattr(ev, "error", ""),
            "traceback": getattr(ev, "traceback", None),
        },
    },
    "LiteAgentExecutionStartedEvent": {
        "type": "agent.started",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_info", {}).get("role", ""),
            "prompt": str(getattr(ev, "messages", ""))[:200],
        },
    },
    "LiteAgentExecutionCompletedEvent": {
        "type": "agent.completed",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_info", {}).get("role", ""),
            "output": getattr(ev, "output", ""),
        },
    },
    "LiteAgentExecutionErrorEvent": {
        "type": "agent.error",
        "extract": lambda ev: {
            "agent_role": getattr(ev, "agent_info", {}).get("role", ""),
            "error": getattr(ev, "error", ""),
        },
    },
}


class BridgeListener:
    """Register on CrewAI's event bus and translate events to the flat JSON
    protocol.

    Every emitted dict is guaranteed to carry a ``crew_id`` field so that
    multi-tab subscribers can filter by active crew.

    Parameters
    ----------
    crew_id : str
        Unique identifier for this execution run.
    on_event : EventCallback
        Callback invoked with ``ProtocolEvent`` dicts.
    """

    def __init__(self, crew_id: str, on_event: EventCallback) -> None:
        super().__init__()
        self._crew_id = crew_id
        self._on_event = on_event
        self._handlers: dict[str, Any] = {}
        self._registered = False

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def register(self) -> None:
        """Subscribe to all supported CrewAI events on ``crewai_event_bus``."""
        if self._registered:
            return
        if _crewai_event_bus is None:
            return  # Noop when CrewAI is not installed

        event_classes = self._discover_event_classes()

        for event_name, mapping in _CREWAI_EVENT_MAP.items():
            event_cls = event_classes.get(event_name)
            if event_cls is None:
                continue
            handler = self._make_handler(mapping)
            self._handlers[event_name] = handler
            try:
                _crewai_event_bus.on(event_cls, handler)
            except Exception:
                pass

        self._registered = True

    def unregister(self) -> None:
        """Remove all registered handlers from ``crewai_event_bus``."""
        if not self._registered or _crewai_event_bus is None:
            return
        event_classes = self._discover_event_classes()
        for event_name, handler in self._handlers.items():
            event_cls = event_classes.get(event_name)
            if event_cls is not None:
                try:
                    _crewai_event_bus.off(event_cls, handler)
                except Exception:
                    pass
        self._handlers.clear()
        self._registered = False

    def setup_listeners(self, crewai_event_bus: Any) -> None:
        """Called by CrewAI internals (part of BaseEventListener protocol)."""
        pass

    # ------------------------------------------------------------------ #
    #  Test helpers (for unit tests)
    # ------------------------------------------------------------------ #

    def _discover_event_classes(self) -> dict[str, type]:
        """Introspect ``crewai.events`` for known event class names."""
        classes: dict[str, type] = {}
        try:
            import crewai.events as events_mod  # type: ignore[import-untyped]
        except ImportError:
            return classes

        for name in _CREWAI_EVENT_MAP:
            cls = getattr(events_mod, name, None)
            if cls is not None and isinstance(cls, type):
                classes[name] = cls
        return classes

    def _make_handler(self, mapping: dict[str, Any]) -> Callable[[Any], None]:
        """Create a closure that translates a CrewAI event object to a
        protocol dict and invokes ``on_event``."""
        event_type: str = mapping["type"]
        extract = mapping["extract"]
        crew_id = self._crew_id
        on_event = self._on_event

        def _handler(event_obj: Any) -> None:
            payload: ProtocolEvent = {"type": event_type, "crew_id": crew_id}
            try:
                payload.update(extract(event_obj))
            except Exception:
                pass
            payload["ts"] = time.time()
            try:
                on_event(payload)
            except Exception:
                pass

        return _handler


# ═══════════════════════════════════════════════════════════════════════════
#  CrewEngine  —  execution entry point
# ═══════════════════════════════════════════════════════════════════════════

class CrewEngine:
    """Orchestrate CrewAI executions on background threads.

    Usage::

        engine = CrewEngine()
        def on_event(evt: dict) -> None:
            print(evt["type"], evt["crew_id"])

        handle = engine.run(crew_model, {"topic": "AI"}, on_event=on_event)
        # ... wait or poll ...
        engine.stop(handle)   # or let the thread finish naturally
    """

    _DEFAULT_JOIN_TIMEOUT = 5  # seconds

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        crew_model: models.CrewModel,
        inputs: dict[str, Any] | None = None,
        *,
        on_event: EventCallback,
    ) -> ExecutionHandle:
        """Start a crew execution on a background thread.

        Parameters
        ----------
        crew_model : models.CrewModel
            Validated and complete crew configuration.
        inputs : dict | None
            Input variables for the crew.
        on_event : EventCallback
            Called for every execution event emitted by the bridge listener.

        Returns
        -------
        ExecutionHandle
            Handle to query / cancel the running execution.
        """
        crew_id = str(uuid.uuid4())
        flag: StopFlag = {"stop": False}
        listener = BridgeListener(crew_id=crew_id, on_event=on_event)

        def _worker() -> None:
            try:
                listener.register()
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(
                        self._kickoff(
                            crew_model, inputs or {}, flag, on_event, crew_id
                        )
                    )
                finally:
                    loop.close()
            except Exception as exc:
                on_event({
                    "type": "crew.error",
                    "crew_id": crew_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "failed_task": None,
                    "ts": time.time(),
                })
            finally:
                listener.unregister()

        thread = threading.Thread(
            target=_worker, daemon=True, name=f"crew-{crew_id[:8]}"
        )
        thread.start()

        return ExecutionHandle(
            thread=thread,
            flag=flag,
            listener=listener,
            crew_id=crew_id,
        )

    def stop(self, handle: ExecutionHandle) -> None:
        """Request cancellation and wait for the thread to finish."""
        handle.flag["stop"] = True
        if handle.thread.is_alive():
            handle.thread.join(timeout=self._DEFAULT_JOIN_TIMEOUT)

    def test_agent(
        self,
        crew_model: models.CrewModel,
        agent_role: str,
        prompt: str,
        *,
        on_event: EventCallback,
    ) -> ExecutionHandle:
        """Run a single agent on a background thread (playground support).

        Starts a daemon thread that builds the agent, registers a
        :class:`BridgeListener` on the CrewAI event bus, and runs
        ``kickoff_async``. Events (``agent.started``, ``agent.completed``,
        ``agent.error``, ``agent.stopped``) are emitted via ``on_event``.

        Parameters
        ----------
        crew_model : models.CrewModel
            Crew config containing the agent definition.
        agent_role : str
            Role string of the agent to test.
        prompt : str
            Prompt to send to the agent.
        on_event : EventCallback
            Required callback for streaming tokens and lifecycle events.

        Returns
        -------
        ExecutionHandle
            Handle to query / cancel the running execution.
        """
        agent_model = None
        for a in crew_model.agents:
            if a.role == agent_role:
                agent_model = a
                break
        if agent_model is None:
            raise ValueError(
                f"No agent with role '{agent_role}' found in crew "
                f"'{crew_model.name}'"
            )

        crew_id = f"pg-{uuid.uuid4().hex[:12]}"
        flag: StopFlag = {"stop": False}
        listener = BridgeListener(crew_id=crew_id, on_event=on_event)

        def _worker() -> None:
            try:
                listener.register()
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(
                        self._test_agent_coro(
                            agent_model, prompt, flag, on_event, crew_id
                        )
                    )
                finally:
                    loop.close()
            except Exception as exc:
                on_event({
                    "type": "agent.error",
                    "crew_id": crew_id,
                    "agent_role": agent_role,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "ts": time.time(),
                })
            finally:
                listener.unregister()

        thread = threading.Thread(
            target=_worker, daemon=True, name=f"test-agent-{crew_id[:8]}"
        )
        thread.start()

        return ExecutionHandle(
            thread=thread,
            flag=flag,
            listener=listener,
            crew_id=crew_id,
        )

    async def _test_agent_coro(
        self,
        agent_model: models.AgentModel,
        prompt: str,
        flag: StopFlag,
        on_event: EventCallback,
        crew_id: str,
    ) -> None:
        """Async coroutine that builds and runs a single agent.

        Emits synthetic lifecycle events (``agent.started``,
        ``agent.completed``, ``agent.error``, ``agent.stopped``) so
        callers get guaranteed event coverage even if the CrewAI event
        bus is silent.
        """
        agent_kwargs: dict[str, Any] = {
            "role": agent_model.role,
            "goal": agent_model.goal,
            "backstory": agent_model.backstory or "",
            "allow_delegation": False,
        }
        llm = Adapter._build_llm(agent_model.llm)
        if llm is not None:
            agent_kwargs["llm"] = llm
        if agent_model.tools:
            agent_kwargs["tools"] = [
                Adapter._resolve_tool(t) for t in agent_model.tools
            ]
        if agent_model.max_iter is not None:
            agent_kwargs["max_iter"] = agent_model.max_iter
        if agent_model.model_extra:
            agent_kwargs.update(agent_model.model_extra)

        agent = _CrewAIAgent(**agent_kwargs)

        # Emit synthetic agent.started
        on_event({
            "type": "agent.started",
            "crew_id": crew_id,
            "agent_role": agent_model.role,
            "prompt": prompt[:200],
            "ts": time.time(),
        })

        try:
            # Use ``messages=prompt`` (modern CrewAI API)
            result = await agent.kickoff_async(messages=prompt)
            output = str(result) if result else ""

            # Extract token usage (if available)
            token_usage = getattr(result, "token_usage", {}) or {}
            input_toks = token_usage.get("input_tokens", 0)
            output_toks = token_usage.get("output_tokens", 0)

            on_event({
                "type": "agent.completed",
                "crew_id": crew_id,
                "agent_role": agent_model.role,
                "output": output,
                "token_usage": {
                    "input_tokens": input_toks,
                    "output_tokens": output_toks,
                    "total_tokens": input_toks + output_toks,
                },
                "ts": time.time(),
            })
        except CancelledError:
            on_event({
                "type": "agent.stopped",
                "crew_id": crew_id,
                "agent_role": agent_model.role,
                "reason": "User cancelled",
                "ts": time.time(),
            })
        except Exception as exc:
            on_event({
                "type": "agent.error",
                "crew_id": crew_id,
                "agent_role": agent_model.role,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "ts": time.time(),
            })

    def test_task(
        self,
        crew_model: models.CrewModel,
        task_name: str,
        mock_context: str,
    ) -> str:
        """Run a single task with mock context (Fase 2).

        Parameters
        ----------
        crew_model : models.CrewModel
            Crew config containing the task definition.
        task_name : str
            Name of the task to test in isolation.
        mock_context : str
            Mock output text to feed as context.

        Returns
        -------
        str
            Task output text.
        """
        task_model = None
        for t in crew_model.tasks:
            if t.name == task_name:
                task_model = t
                break
        if task_model is None:
            raise ValueError(
                f"No task with name '{task_name}' found in crew "
                f"'{crew_model.name}'"
            )

        return f"[Fase 2] test_task not yet implemented for '{task_name}'"

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    async def _kickoff(
        self,
        crew_model: models.CrewModel,
        inputs: dict[str, Any],
        flag: StopFlag,
        on_event: EventCallback,
        crew_id: str,
    ) -> None:
        """Core async execution coroutine."""
        crew = Adapter.build_crewai_object(
            crew_model,
            flag=flag,
            on_event=on_event,
            crew_id=crew_id,
        )

        # Emit crew.started
        on_event({
            "type": "crew.started",
            "crew_id": crew_id,
            "crew_name": crew_model.name,
            "task_count": len(crew_model.tasks),
            "ts": time.time(),
        })

        try:
            result = await crew.kickoff_async(inputs=inputs)
            # Emit crew.completed
            token_usage = getattr(result, "token_usage", {}) or {}
            model_name = ""
            for agent in crew_model.agents:
                if agent.llm:
                    model_name = agent.llm.model
                    break

            input_toks = token_usage.get("input_tokens", 0)
            output_toks = token_usage.get("output_tokens", 0)
            cost = calculate_cost(model_name, input_toks, output_toks)

            on_event({
                "type": "crew.completed",
                "crew_id": crew_id,
                "status": "success",
                "token_usage": {
                    "input_tokens": input_toks,
                    "output_tokens": output_toks,
                    "total_tokens": input_toks + output_toks,
                },
                "cost": cost,
                "ts": time.time(),
            })

            # Emit resource.update
            on_event({
                "type": "resource.update",
                "crew_id": crew_id,
                "task": crew_model.name,
                "agent": "all",
                "tokens_in": input_toks,
                "tokens_out": output_toks,
                "cost": cost,
                "ts": time.time(),
            })
        except CancelledError:
            on_event({
                "type": "crew.stopped",
                "crew_id": crew_id,
                "reason": "User cancelled",
                "ts": time.time(),
            })
        except Exception as exc:
            on_event({
                "type": "crew.error",
                "crew_id": crew_id,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "failed_task": None,
                "ts": time.time(),
            })
