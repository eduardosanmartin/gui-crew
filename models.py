"""Pydantic v2 models for gui-crew — CrewAI configuration layer.

All models use `extra="allow"` for forward compatibility with CrewAI
API evolution. This module is the single source of truth for crew
configuration; UI components and the CrewAI adapter derive from these
models.  It must NOT import `crewai` — that boundary belongs to
`crew_engine.py`.
"""

from __future__ import annotations

import re
import warnings
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ═══════════════════════════════════════════════
#  Base / Leaf Models
# ═══════════════════════════════════════════════

class LLMModel(BaseModel):
    """LLM configuration for agents and the crew manager."""

    model_config = ConfigDict(extra="allow")

    model: str = "openai/gpt-4o"
    temperature: float | None = None
    base_url: str | None = None
    api_key_env: str | None = None


class MemoryConfig(BaseModel):
    """Memory settings for agents and crews."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    recency_weight: float | None = None
    semantic_weight: float | None = None
    importance_weight: float | None = None
    recency_half_life_days: int | None = None
    embedder: dict[str, Any] | None = None


class ToolRef(BaseModel):
    """Reference to a CrewAI tool — built-in or custom."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["builtin", "custom"]
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    # custom tools only
    args_schema: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Tool name must not be empty")
        return v.strip()


class InputVar(BaseModel):
    """Input variable definition for crew execution."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str = "str"
    description: str | None = None
    default: Any | None = None

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Input variable name must not be empty")
        return v.strip()


# ═══════════════════════════════════════════════
#  Core Entity Models
# ═══════════════════════════════════════════════

class AgentModel(BaseModel):
    """Agent configuration — maps to ``crewai.Agent``."""

    model_config = ConfigDict(extra="allow")

    role: str
    goal: str
    backstory: str = ""
    llm: LLMModel | None = None
    function_calling_llm: LLMModel | None = None
    tools: list[ToolRef] = Field(default_factory=list)
    allow_delegation: bool = False
    allow_code_execution: bool = False
    max_iter: int | None = Field(default=None, ge=0)
    memory: bool | MemoryConfig = False
    system_template: str | None = None
    multimodal: bool = False

    @field_validator("role", "goal")
    @classmethod
    def _required_not_empty(cls, v: str, info: Any) -> str:
        if not v.strip():
            raise ValueError(f"Agent {info.field_name} must not be empty")
        return v.strip()


class TaskModel(BaseModel):
    """Task configuration — maps to ``crewai.Task``.

    ``context`` is a list of task **names** that must complete before
    this task can execute (DAG edges).
    """

    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    expected_output: str
    agent_role: str | None = None
    context: list[str] = Field(default_factory=list)
    output_file: str | None = None
    output_json_schema: dict | None = None  # JSON Schema for structured output
    human_input: bool = False
    async_execution: bool = False
    guardrails: list[str] = Field(default_factory=list)
    guardrail_max_retries: int = Field(default=3, ge=0)
    tools: list[ToolRef] = Field(default_factory=list)
    markdown: bool = False

    @field_validator("name", "description", "expected_output")
    @classmethod
    def _required_not_empty(cls, v: str, info: Any) -> str:
        if not v.strip():
            raise ValueError(f"Task {info.field_name} must not be empty")
        return v.strip()

    @field_validator("output_file")
    @classmethod
    def _output_file_safe(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.startswith("/"):
            raise ValueError("output_file must be a relative path, got absolute")
        if ".." in v.split("/"):
            raise ValueError("output_file must not contain parent-dir '..' segments")
        # Also check for leading ../
        normalized = v.replace("\\", "/")
        segments = normalized.split("/")
        if ".." in segments:
            raise ValueError("output_file must not contain parent-dir '..' segments")
        return v

    @field_validator("context")
    @classmethod
    def _context_no_empty(cls, v: list[str]) -> list[str]:
        for entry in v:
            if not entry.strip():
                raise ValueError("Context entries must not be empty")
        return v


# ═══════════════════════════════════════════════
#  Crew Model — with cross-entity validators
# ═══════════════════════════════════════════════

class CrewModel(BaseModel):
    """Crew configuration — maps to ``crewai.Crew``.

    Validation rules (enforced on construction / model validation):
    * Hierarchical process requires ``manager_llm`` or ``manager_agent_role``.
    * A task cannot list itself in its own ``context``.
    * Tasks must not form a directed cycle (Kahn's algorithm).
    * ``{variable}`` references in agent/task text SHOULD match ``inputs``
      (warning-level — non-blocking).
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    name: str
    description: str = ""
    process: Literal["sequential", "hierarchical"] = "sequential"
    agents: list[AgentModel] = Field(default_factory=list)
    tasks: list[TaskModel] = Field(default_factory=list)
    memory: bool | MemoryConfig = False
    planning: bool = False
    manager_llm: LLMModel | None = None
    manager_agent_role: str | None = None
    knowledge_sources: list[dict[str, Any]] = Field(default_factory=list)
    embedder: dict[str, Any] | None = None
    inputs: list[InputVar] = Field(default_factory=list)
    callbacks: dict[str, Any] = Field(default_factory=dict)
    verbose: bool = False

    # -- basic validators ---------------------------------------------------

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Crew name must not be empty")
        return v.strip()

    @field_validator("manager_agent_role")
    @classmethod
    def _manager_role_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("manager_agent_role must not be empty or whitespace-only")
            return stripped
        return v

    # -- cross-field validators ---------------------------------------------

    @model_validator(mode="after")
    def _validate_hierarchical_requires_manager(self) -> CrewModel:
        if self.process == "hierarchical":
            if self.manager_llm is None and not self.manager_agent_role:
                raise ValueError(
                    "Hierarchical process requires at least one of: "
                    "manager_llm or manager_agent_role"
                )
        return self

    @model_validator(mode="after")
    def _validate_no_task_self_context(self) -> CrewModel:
        for task in self.tasks:
            if task.name in task.context:
                raise ValueError(
                    f"Task '{task.name}' cannot depend on itself"
                )
        return self

    @model_validator(mode="after")
    def _validate_no_context_cycles(self) -> CrewModel:
        """Detect cycles in the task-context DAG (Kahn's algorithm).

        Uses **task indices** (not names) for internal graph
        representation so that duplicate task names do not produce
        false-positive cycle reports.
        """
        if len(self.tasks) <= 1:
            return self

        n = len(self.tasks)

        # Map task name → list of task indices that share that name.
        name_to_indices: dict[str, list[int]] = {}
        for i, task in enumerate(self.tasks):
            name_to_indices.setdefault(task.name, []).append(i)

        # Adjacency and in-degree built on array indices.
        adj: list[list[int]] = [[] for _ in range(n)]
        in_degree: list[int] = [0] * n

        for i, task in enumerate(self.tasks):
            for ctx_name in task.context:
                for src_idx in name_to_indices.get(ctx_name, []):
                    if src_idx == i:
                        continue  # self-loop — caught by dedicated validator
                    adj[src_idx].append(i)
                    in_degree[i] += 1

        # Kahn's algorithm — O(V+E) with deque
        queue = deque(i for i, d in enumerate(in_degree) if d == 0)
        processed = 0
        while queue:
            node = queue.popleft()
            processed += 1
            for neighbour in adj[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if processed != n:
            cycle_path = self._find_cycle_path_idx(adj, in_degree)
            raise ValueError(
                f"Circular dependency detected in task context: {cycle_path}"
            )

        return self

    @model_validator(mode="after")
    def _validate_variable_references(self) -> CrewModel:
        """Warn about ``{variable}`` references with no matching input."""
        input_names = {v.name for v in self.inputs}
        var_pattern = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")

        entities: list[tuple[str, str, str | None]] = []
        for agent in self.agents:
            entities.append(("agent", agent.role, agent.goal))
            entities.append(("agent", agent.role, agent.backstory))
            if agent.system_template:
                entities.append(("agent", agent.role, agent.system_template))

        for task in self.tasks:
            entities.append(("task", task.name, task.description))
            entities.append(("task", task.name, task.expected_output))

        for entity_kind, entity_id, text in entities:
            if not text:
                continue
            refs = var_pattern.findall(text)
            for ref in refs:
                if ref not in input_names:
                    warnings.warn(
                        f"Variable '{{{ref}}}' in {entity_kind} "
                        f"'{entity_id}' does not match any crew input. "
                        f"Available inputs: {sorted(input_names) or 'none'}",
                        RuntimeWarning,
                    )

        return self

    @model_validator(mode="after")
    def _validate_agent_roles_exist(self) -> CrewModel:
        """Each task's ``agent_role`` must reference an agent that exists."""
        agent_roles = {a.role for a in self.agents}
        for task in self.tasks:
            if task.agent_role is not None and task.agent_role not in agent_roles:
                raise ValueError(
                    f"Task '{task.name}' references agent_role "
                    f"'{task.agent_role}' but no agent has that role. "
                    f"Available roles: {sorted(agent_roles) or 'none'}"
                )
        return self

    # -- cycle-reporting helper ---------------------------------------------

    def _find_cycle_path_idx(
        self, adj: list[list[int]], in_degree: list[int]
    ) -> str:
        """DFS helper — returns a human-readable cycle path using task
        **names**, e.g. ``Research → Write → Analyze → Research``.

        Works on index-based adjacency / in-degree (from
        ``_validate_no_context_cycles``)."""
        remaining = {i for i, d in enumerate(in_degree) if d > 0}
        if not remaining:
            return "unknown cycle"

        visited: set[int] = set()
        path: list[int] = []

        def _dfs(node: int) -> list[int] | None:
            if node in path:
                start = path.index(node)
                return path[start:] + [node]
            if node in visited:
                return None
            visited.add(node)
            path.append(node)
            for neighbour in adj[node]:
                result = _dfs(neighbour)
                if result:
                    return result
            path.pop()
            return None

        for start in remaining:
            cycle_indices = _dfs(start)
            if cycle_indices:
                names = [self.tasks[i].name for i in cycle_indices]
                return " → ".join(names)

        return "unknown cycle"

    # ═════════════════════════════════════════════
    #  Serialization adapters
    # ═════════════════════════════════════════════

    # -- to-dict (shared) ---------------------------------------------------

    def _to_crewai_dict(self) -> dict[str, Any]:
        """Convert to a CrewAI-compatible dictionary."""
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "process": self.process,
            "agents": [a.role for a in self.agents],
            "tasks": [],
        }

        # agent_config — full agent definitions keyed by role
        agent_config: dict[str, dict[str, Any]] = {}
        for agent in self.agents:
            a: dict[str, Any] = {
                "role": agent.role,
                "goal": agent.goal,
            }
            if agent.backstory:
                a["backstory"] = agent.backstory
            if agent.llm:
                a["llm"] = agent.llm.model_dump(exclude_defaults=True)
            if agent.function_calling_llm:
                a["function_calling_llm"] = (
                    agent.function_calling_llm.model_dump(exclude_defaults=True)
                )
            if agent.tools:
                a["tools"] = [t.model_dump(exclude_defaults=True) for t in agent.tools]
            if agent.allow_delegation:
                a["allow_delegation"] = True
            if agent.allow_code_execution:
                a["allow_code_execution"] = True
            if agent.max_iter is not None:
                a["max_iter"] = agent.max_iter
            if agent.memory:
                a["memory"] = (
                    agent.memory.model_dump(exclude_defaults=True)
                    if isinstance(agent.memory, MemoryConfig)
                    else True
                )
            if agent.system_template:
                a["system_template"] = agent.system_template
            if agent.multimodal:
                a["multimodal"] = True
            if agent.model_extra:
                a.update(agent.model_extra)
            agent_config[agent.role] = a
        if agent_config:
            result["agent_config"] = agent_config

        # crew-level extras
        if self.memory:
            result["memory"] = (
                self.memory.model_dump(exclude_defaults=True)
                if isinstance(self.memory, MemoryConfig)
                else True
            )
        if self.planning:
            result["planning"] = True
        if self.verbose:
            result["verbose"] = True
        if self.manager_llm:
            result["manager_llm"] = self.manager_llm.model_dump(
                exclude_defaults=True
            )
        if self.manager_agent_role:
            result["manager_agent"] = self.manager_agent_role
        if self.knowledge_sources:
            result["knowledge_sources"] = self.knowledge_sources
        if self.embedder:
            result["embedder"] = self.embedder
        if self.inputs:
            result["inputs"] = {v.name: v.default for v in self.inputs}
        if self.callbacks:
            result["callbacks"] = self.callbacks
        if self.model_extra:
            result.update(self.model_extra)

        # tasks
        for task in self.tasks:
            t: dict[str, Any] = {
                "name": task.name,
                "description": task.description,
                "expected_output": task.expected_output,
            }
            if task.agent_role:
                t["agent"] = task.agent_role
            if task.context:
                t["context"] = task.context
            if task.output_file:
                t["output_file"] = task.output_file
            if task.output_json_schema:
                t["output_json_schema"] = task.output_json_schema
            if task.human_input:
                t["human_input"] = True
            if task.async_execution:
                t["async_execution"] = True
            if task.guardrails:
                t["guardrails"] = task.guardrails
            if task.guardrail_max_retries != 3:
                t["guardrail_max_retries"] = task.guardrail_max_retries
            if task.tools:
                t["tools"] = [tool.model_dump() for tool in task.tools]
            if task.markdown:
                t["markdown"] = True
            result["tasks"].append(t)

        return result

    # -- CrewAI Python import/export ----------------------------------------

    @classmethod
    def from_crewai_python(cls, content: str, filename: str = "<string>") -> CrewModel:
        """Parse a CrewAI Python source file into a CrewModel.

        Delegates to ``crewai_python_parser.parse_file``.  Supports
        classic-style files with inline ``Agent()`` / ``Task()`` /
        ``Crew()`` constructor calls and variable references.

        Args:
            content: Python source code as a string.
            filename: Source filename for error messages.

        Returns:
            Parsed CrewModel instance ready for the Builder.

        Raises:
            ParseError: If required definitions are missing or the
                file uses ``@CrewBase`` (decorator) style.
            SyntaxError: If the source has invalid Python syntax.
        """
        from crewai_python_parser import parse_file

        return parse_file(content, filename)

    @classmethod
    def from_crewai_python_file(cls, path: str) -> CrewModel:
        """Parse a CrewAI Python file from disk into a CrewModel.

        Convenience wrapper around ``from_crewai_python`` that reads
        the file first.

        Args:
            path: Path to a ``.py`` file on disk.

        Returns:
            Parsed CrewModel instance.
        """
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        return cls.from_crewai_python(content, filename=path)

    @staticmethod
    def scan_crewai_directory(path: str) -> list[Any]:
        """Recursively scan *path* for CrewAI ``.py`` crew files.

        Delegates to ``crewai_python_parser.scan_directory``.

        Args:
            path: Directory to scan.

        Returns:
            List of ``CrewScanResult`` named tuples.
        """
        from pathlib import Path
        from crewai_python_parser import scan_directory

        return scan_directory(Path(path))

    def to_crewai_python(
        self,
        style: str = "decorator",
        include_yaml: bool = True,
        include_tools_stubs: bool = True,
    ) -> bytes:
        """Generate a runnable CrewAI Python project as ZIP bytes.

        Delegates to ``crewai_code_generator.generate_zip``.

        Args:
            style: ``"decorator"`` (default, ``@CrewBase`` pattern) or
                ``"classic"`` (inline constructors).
            include_yaml: If ``True``, include ``config/*.yaml`` files
                (decorator style only).
            include_tools_stubs: If ``True``, include ``tools/custom_tool.py``
                with TODO placeholders for any custom tools.

        Returns:
            Bytes of the generated ZIP file.
        """
        from crewai_code_generator import generate_zip

        return generate_zip(
            self,
            style=style,
            include_yaml=include_yaml,
            include_tools_stubs=include_tools_stubs,
        )

    # -- public serialization -----------------------------------------------

    def to_crewai_json(self, indent: int = 2) -> str:
        """Serialize to a CrewAI-compatible JSON string."""
        import json

        return json.dumps(
            self._to_crewai_dict(),
            indent=indent,
            ensure_ascii=False,
            default=str,
        )

    def to_crewai_yaml(self) -> str:
        """Serialize to a CrewAI-compatible YAML string."""
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML serialization. "
                "Install it with: pip install pyyaml"
            ) from exc

        return yaml.dump(
            self._to_crewai_dict(),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    # -- public deserialization ---------------------------------------------

    @classmethod
    def from_crewai_json(cls, json_str: str) -> CrewModel:
        """Deserialize from a CrewAI-compatible JSON string."""
        import json

        data = json.loads(json_str)
        return cls._from_crewai_dict(data)

    @classmethod
    def from_crewai_yaml(cls, yaml_str: str) -> CrewModel:
        """Deserialize from a CrewAI-compatible YAML string."""
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML deserialization. "
                "Install it with: pip install pyyaml"
            ) from exc

        data = yaml.safe_load(yaml_str)
        return cls._from_crewai_dict(data)

    # -- internal factory ---------------------------------------------------

    @classmethod
    def _from_crewai_dict(cls, data: dict[str, Any]) -> CrewModel:
        """Build a ``CrewModel`` from a CrewAI-compatible dictionary."""
        # helpers -----------------------------------------------------------
        def _parse_llm(raw: Any) -> LLMModel | None:
            if isinstance(raw, dict):
                return LLMModel(**raw)
            return None

        def _parse_memory(raw: Any) -> bool | MemoryConfig:
            if isinstance(raw, dict):
                return MemoryConfig(**raw)
            if isinstance(raw, bool):
                return raw
            return False

        def _parse_tools(raw: Any) -> list[ToolRef]:
            if isinstance(raw, list):
                return [ToolRef(**t) for t in raw]
            return []

        # known fields for each model to compute extra kwargs -----------------
        _AGENT_KNOWN = frozenset({
            "role", "goal", "backstory", "llm", "function_calling_llm",
            "tools", "allow_delegation", "allow_code_execution",
            "max_iter", "memory", "system_template", "multimodal",
        })

        _TASK_KNOWN = frozenset({
            "name", "description", "expected_output", "agent_role", "agent",
            "context", "output_file", "output_json", "output_json_schema",
            "human_input", "async_execution", "guardrails",
            "guardrail_max_retries", "tools", "markdown",
        })

        _CREW_KNOWN = frozenset({
            "schema_version", "name", "description", "process", "agents",
            "agent_config", "tasks", "memory", "planning", "manager_llm",
            "manager_agent_role", "manager_agent", "knowledge_sources",
            "embedder", "inputs", "callbacks", "verbose",
        })

        # agents ------------------------------------------------------------
        agents: list[AgentModel] = []
        agent_configs: dict[str, Any] = data.get("agent_config", {})
        for role_name in data.get("agents", []):
            a_data = agent_configs.get(role_name, {})
            agent_kwargs: dict[str, Any] = {
                "role": a_data.get("role", role_name),
                "goal": a_data.get("goal", ""),
                "backstory": a_data.get("backstory", ""),
                "llm": _parse_llm(a_data.get("llm")),
                "function_calling_llm": _parse_llm(
                    a_data.get("function_calling_llm")
                ),
                "tools": _parse_tools(a_data.get("tools")),
                "allow_delegation": a_data.get("allow_delegation", False),
                "allow_code_execution": a_data.get(
                    "allow_code_execution", False
                ),
                "max_iter": a_data.get("max_iter"),
                "memory": _parse_memory(a_data.get("memory")),
                "system_template": a_data.get("system_template"),
                "multimodal": a_data.get("multimodal", False),
            }
            # Pass any remaining keys as extra kwargs → lands in model_extra
            for k, v in a_data.items():
                if k not in _AGENT_KNOWN:
                    agent_kwargs[k] = v
            agents.append(AgentModel(**agent_kwargs))

        # tasks -------------------------------------------------------------
        tasks: list[TaskModel] = []
        for t_data in data.get("tasks", []):
            task_kwargs: dict[str, Any] = {
                "name": t_data.get("name", ""),
                "description": t_data.get("description", ""),
                "expected_output": t_data.get("expected_output", ""),
                "agent_role": t_data.get(
                    "agent", t_data.get("agent_role")
                ),
                "context": t_data.get("context", []),
                "output_file": t_data.get("output_file"),
                "output_json_schema": t_data.get("output_json_schema"),
                "human_input": t_data.get("human_input", False),
                "async_execution": t_data.get("async_execution", False),
                "guardrails": t_data.get("guardrails", []),
                "guardrail_max_retries": t_data.get(
                    "guardrail_max_retries", 3
                ),
                "tools": _parse_tools(t_data.get("tools")),
                "markdown": t_data.get("markdown", False),
            }
            for k, v in t_data.items():
                if k not in _TASK_KNOWN:
                    task_kwargs[k] = v
            tasks.append(TaskModel(**task_kwargs))

        # inputs ------------------------------------------------------------
        inputs_raw = data.get("inputs", [])
        if isinstance(inputs_raw, dict):
            inputs = [
                InputVar(name=k, default=v)
                for k, v in inputs_raw.items()
            ]
        elif isinstance(inputs_raw, list):
            inputs = [InputVar(**v) for v in inputs_raw]
        else:
            inputs = []

        # crew-level extras -------------------------------------------------
        crew_kwargs: dict[str, Any] = {
            "schema_version": data.get("schema_version", 1),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "process": data.get("process", "sequential"),
            "agents": agents,
            "tasks": tasks,
            "memory": _parse_memory(data.get("memory")),
            "planning": data.get("planning", False),
            "manager_llm": _parse_llm(data.get("manager_llm")),
            "manager_agent_role": data.get(
                "manager_agent", data.get("manager_agent_role")
            ),
            "knowledge_sources": data.get("knowledge_sources", []),
            "embedder": data.get("embedder"),
            "inputs": inputs,
            "callbacks": data.get("callbacks", {}),
            "verbose": data.get("verbose", False),
        }
        for k, v in data.items():
            if k not in _CREW_KNOWN:
                crew_kwargs[k] = v

        return cls(**crew_kwargs)


# ═══════════════════════════════════════════════
#  Run Record — execution history metadata
# ═══════════════════════════════════════════════

class TokenUsage(BaseModel):
    """Token usage summary for a crew run."""

    model_config = ConfigDict(extra="allow")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class RunRecord(BaseModel):
    """Execution history record — stores metadata about a completed crew run.

    ``crew_snapshot`` holds the full serialized ``CrewModel`` at the time
    of execution to enable later comparison / reproduction.
    """

    model_config = ConfigDict(extra="allow")

    crew_name: str
    crew_snapshot: dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int = 0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: float = 0.0
    status: Literal["success", "failed", "cancelled", "running"] = "running"
    error: str | None = None
    crewai_version: str = ""

    @model_validator(mode="after")
    def _status_error_consistency(self):
        """Ensure status and error fields are consistent."""
        if self.status == "success" and self.error:
            raise ValueError("Success status cannot have error message")
        if self.status == "failed" and not self.error:
            raise ValueError("Failed status must have error message")
        return self
