"""CrewAI Python project code generator for gui-crew.

Generates a runnable CrewAI Python project as an in-memory ZIP archive
from a CrewModel instance.  Supports two output styles:

* **decorator** (default): ``@CrewBase`` / ``@agent`` / ``@task``
  with YAML config files.
* **classic**: inline ``Agent()`` / ``Task()`` / ``Crew()`` constructor
  calls (all data in Python source).

Round-trip fidelity (what survives export → import):
  Preserved — roles, goals, backstories, task descriptions,
  expected outputs, context DAG edges, builtin tool names.
  Lost     — callbacks, ``output_json_schema`` dicts, custom tool
  parameters, complex memory configs, knowledge sources.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from models import AgentModel, CrewModel, TaskModel

# ═══════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════

CREWAI_VERSION_HEADER = "# Generated for CrewAI >= 1.15\n"

_LOSSY_WARNING = (
    "# ══ Lossy export ══\n"
    "# The following fields are NOT preserved in generated code:\n"
    "#   callbacks, output_json_schema, complex memory configs,\n"
    "#   custom tool parameters, knowledge_sources, embedder.\n"
    "# Round-trip preserves: roles, goals, backstories, task\n"
    "# descriptions, expected outputs, context DAG, builtin tools.\n"
    "#\n"
)

_ID_CLEAN = re.compile(r"[^a-zA-Z0-9_]+")

_PYTHON_KEYWORDS = frozenset({
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield",
})


# ═══════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════


def generate_zip(
    crew: CrewModel,
    style: str = "decorator",
    include_yaml: bool = True,
    include_tools_stubs: bool = True,
) -> bytes:
    """Generate a ready-to-run CrewAI Python project as a ZIP archive.

    Archive contents::

        crew.py         — crew definition
        agents.py       — agent definitions
        tasks.py        — task definitions
        main.py         — entry-point script
        config/         — YAML configs (decorator, optional)
        tools/          — custom-tool stubs (optional)

    Args:
        crew: Source CrewModel to export.
        style: ``"decorator"`` (default) or ``"classic"``.
        include_yaml: If ``True`` and style is ``"decorator"``, include
            ``config/agents.yaml`` and ``config/tasks.yaml``.
        include_tools_stubs: If ``True``, include ``tools/custom_tool.py``
            with TODO placeholders for any referenced custom tools.

    Returns:
        ZIP file bytes ready for ``ui.download``.

    Raises:
        ValueError: If *style* is not ``"decorator"`` or ``"classic"``.
    """
    if style not in ("decorator", "classic"):
        raise ValueError(
            f"Unknown style '{style}'. Use 'decorator' or 'classic'."
        )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("crew.py", _gen_crew_py(crew, style))
        zf.writestr("agents.py", _gen_agents_py(crew.agents, style))
        zf.writestr("tasks.py", _gen_tasks_py(crew.tasks, style))
        zf.writestr("main.py", _gen_main_py(crew, style))

        if include_yaml and style == "decorator":
            zf.writestr("config/agents.yaml", _gen_agents_yaml(crew.agents))
            zf.writestr("config/tasks.yaml", _gen_tasks_yaml(crew.tasks))

        if include_tools_stubs:
            tool_names = _collect_custom_tools(crew)
            if tool_names:
                zf.writestr("tools/custom_tool.py", _gen_tool_stub(tool_names))

    return buffer.getvalue()


# ═══════════════════════════════════════════════
#  crew.py
# ═══════════════════════════════════════════════


def _gen_crew_py(crew: CrewModel, style: str) -> str:
    """Dispatch crew.py generation by style."""
    if style == "decorator":
        return _gen_crew_py_decorator(crew)
    return _gen_crew_py_classic(crew)


def _gen_crew_py_decorator(crew: CrewModel) -> str:
    """crew.py — @CrewBase class with @agent/@task/@crew methods."""
    class_name = _to_class_name(crew.name)

    # @agent methods
    agent_parts: list[str] = []
    for agent in crew.agents:
        safe = _to_identifier(agent.role)
        kwargs = [f'config=self.agents_config["{agent.role}"]']
        if agent.allow_delegation:
            kwargs.append("allow_delegation=True")
        if agent.allow_code_execution:
            kwargs.append("allow_code_execution=True")
        if agent.max_iter is not None:
            kwargs.append(f"max_iter={agent.max_iter}")
        if agent.multimodal:
            kwargs.append("multimodal=True")
        kw_body = _indent(",\n".join(kwargs), 3)
        agent_parts.append(
            f"    @agent\n"
            f"    def {safe}(self) -> Agent:\n"
            f"        return Agent(\n"
            f"{kw_body}\n"
            f"        )"
        )

    # @task methods
    task_parts: list[str] = []
    for task in crew.tasks:
        safe = _to_identifier(task.name)
        kwargs = [f'config=self.tasks_config["{task.name}"]']
        if task.context:
            ctx = ", ".join(f'"{c}"' for c in task.context)
            kwargs.append(f"context=[{ctx}]")
        kw_body = _indent(",\n".join(kwargs), 3)
        task_parts.append(
            f"    @task\n"
            f"    def {safe}(self) -> Task:\n"
            f"        return Task(\n"
            f"{kw_body}\n"
            f"        )"
        )

    # @crew method
    crew_kwargs = [
        "agents=self.agents",
        "tasks=self.tasks",
        f"process=Process.{crew.process}",
    ]
    if crew.verbose:
        crew_kwargs.append("verbose=True")
    if crew.planning:
        crew_kwargs.append("planning=True")
    if crew.memory and crew.memory is not False:
        crew_kwargs.append("memory=True")
    crew_kw_body = _indent(",\n".join(crew_kwargs), 3)

    agent_block = "\n\n".join(agent_parts) if agent_parts else "    # No agents"
    task_block = "\n\n".join(task_parts) if task_parts else "    # No tasks"

    return (
        f"{CREWAI_VERSION_HEADER}"
        f"{_LOSSY_WARNING}"
        f"from crewai import Agent, Crew, Task, Process\n"
        f"from crewai.project import CrewBase, agent, task, crew\n"
        f"\n\n"
        f"@CrewBase\n"
        f"class {class_name}:\n"
        f'    agents_config = "config/agents.yaml"\n'
        f'    tasks_config = "config/tasks.yaml"\n'
        f"\n"
        f"{agent_block}\n"
        f"\n"
        f"{task_block}\n"
        f"\n"
        f"    @crew\n"
        f"    def crew(self) -> Crew:\n"
        f"        return Crew(\n"
        f"{crew_kw_body}\n"
        f"        )\n"
    )


def _gen_crew_py_classic(crew: CrewModel) -> str:
    """crew.py — classic Agent()/Task()/Crew() constructors."""
    lines: list[str] = [
        f"{CREWAI_VERSION_HEADER}",
        f"{_LOSSY_WARNING}",
        "from crewai import Agent, Task, Crew, Process",
        "",
        "# -- Agents --",
    ]

    # Agent constructors
    for agent in crew.agents:
        safe = _to_identifier(agent.role)
        kwargs = [
            f'role="{_escape_str(agent.role)}"',
            f'goal="{_escape_str(agent.goal)}"',
        ]
        if agent.backstory:
            kwargs.append(f'backstory="{_escape_str(agent.backstory)}"')
        if agent.tools:
            tool_list = ", ".join(f"{t.name}()" for t in agent.tools)
            kwargs.append(f"tools=[{tool_list}]")
        if agent.allow_delegation:
            kwargs.append("allow_delegation=True")
        if agent.allow_code_execution:
            kwargs.append("allow_code_execution=True")
        if agent.max_iter is not None:
            kwargs.append(f"max_iter={agent.max_iter}")
        if agent.multimodal:
            kwargs.append("multimodal=True")
        body = _indent(",\n".join(kwargs), 1)
        lines.append(f"{safe} = Agent(\n{body}\n)")
        lines.append("")

    # Task constructors
    lines.append("# -- Tasks --")
    for task in crew.tasks:
        safe = _to_identifier(task.name)
        kwargs = [
            f'description="{_escape_str(task.description)}"',
            f'expected_output="{_escape_str(task.expected_output)}"',
        ]
        if task.agent_role:
            kwargs.append(f"agent={_to_identifier(task.agent_role)}")
        if task.context:
            ctx_vars = ", ".join(_to_identifier(c) for c in task.context)
            kwargs.append(f"context=[{ctx_vars}]")
        if task.output_file:
            kwargs.append(f'output_file="{_escape_str(task.output_file)}"')
        if task.human_input:
            kwargs.append("human_input=True")
        if task.async_execution:
            kwargs.append("async_execution=True")
        body = _indent(",\n".join(kwargs), 1)
        lines.append(f"{safe} = Task(\n{body}\n)")
        lines.append("")

    # Crew constructor
    lines.append("# -- Crew --")
    agent_vars = ", ".join(_to_identifier(a.role) for a in crew.agents)
    task_vars = ", ".join(_to_identifier(t.name) for t in crew.tasks)
    crew_kwargs = [
        f'name="{_escape_str(crew.name)}"',
        f"agents=[{agent_vars}]",
        f"tasks=[{task_vars}]",
        f"process=Process.{crew.process}",
    ]
    if crew.verbose:
        crew_kwargs.append("verbose=True")
    if crew.planning:
        crew_kwargs.append("planning=True")
    if crew.description:
        crew_kwargs.append(f'description="{_escape_str(crew.description)}"')
    body = _indent(",\n".join(crew_kwargs), 1)
    safe = _to_identifier(crew.name)
    lines.append(f"{safe} = Crew(\n{body}\n)")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════
#  agents.py
# ═══════════════════════════════════════════════


def _gen_agents_py(agents: list[AgentModel], style: str) -> str:
    """Dispatch agents.py generation by style."""
    if not agents:
        return f"{CREWAI_VERSION_HEADER}# No agents defined.\n"
    if style == "decorator":
        return (
            f"{CREWAI_VERSION_HEADER}"
            f"from crewai import Agent\n"
            f"from crewai.project import agent\n"
            f"\n"
            f"# Agent definitions are in crew.py under @CrewBase.\n"
            f"# Agent data is in config/agents.yaml.\n"
        )
    # Classic: definitions are in crew.py; agents.py is a re-export stub.
    return (
        f"{CREWAI_VERSION_HEADER}"
        f"from crewai import Agent\n"
        f"\n"
        f"# Agent definitions are in crew.py (classic style).\n"
        f"# Import them from there:  from crew import researcher\n"
    )


# ═══════════════════════════════════════════════
#  tasks.py
# ═══════════════════════════════════════════════


def _gen_tasks_py(tasks: list[TaskModel], style: str) -> str:
    """Dispatch tasks.py generation by style."""
    if not tasks:
        return f"{CREWAI_VERSION_HEADER}# No tasks defined.\n"
    if style == "decorator":
        return (
            f"{CREWAI_VERSION_HEADER}"
            f"from crewai import Task\n"
            f"from crewai.project import task\n"
            f"\n"
            f"# Task definitions are in crew.py under @CrewBase.\n"
            f"# Task data is in config/tasks.yaml.\n"
        )
    return (
        f"{CREWAI_VERSION_HEADER}"
        f"from crewai import Task\n"
        f"\n"
        f"# Task definitions are in crew.py (classic style).\n"
        f"# Import them from there:  from crew import research_task\n"
    )


# ═══════════════════════════════════════════════
#  main.py
# ═══════════════════════════════════════════════


def _gen_main_py(crew: CrewModel, style: str) -> str:
    """Generate main.py — entry-point script."""
    name = _escape_str(crew.name)
    class_name = _to_class_name(crew.name)
    var_name = _to_identifier(crew.name)

    # Build inputs
    if crew.inputs:
        items = _format_input_items(crew)
        inputs_block = f"    inputs = {{{items}}}\n    result = crew.kickoff(inputs=inputs)\n"
    else:
        inputs_block = "    result = crew.kickoff()\n"

    if style == "decorator":
        instantiation = f"crew = {class_name}().crew()"
        import_name = class_name
    else:
        instantiation = f"crew = {var_name}"
        import_name = var_name

    return (
        f"#!/usr/bin/env python3\n"
        f'"""Entry point for the {name} crew."""\n'
        f"\n"
        f"import sys\n"
        f"from crew import {import_name}\n"
        f"\n\n"
        f"def run():\n"
        f'    """Run the crew and print results."""\n'
        f"    {instantiation}\n"
        f"{inputs_block}"
        f'    print("\\n\\n=== Crew Result ===")\n'
        f"    print(result)\n"
        f"    return result\n"
        f"\n\n"
        f'if __name__ == "__main__":\n'
        f"    run()\n"
    )


# ═══════════════════════════════════════════════
#  YAML generators
# ═══════════════════════════════════════════════


def _gen_agents_yaml(agents: list[AgentModel]) -> str:
    """Generate config/agents.yaml."""
    if not agents:
        return "# No agents defined.\n"
    lines: list[str] = []
    for agent in agents:
        lines.append(f"{agent.role}:")
        lines.append(_yaml_str("role", agent.role, 1))
        lines.append(_yaml_str("goal", agent.goal, 1))
        if agent.backstory:
            lines.append(_yaml_str("backstory", agent.backstory, 1))
        if agent.allow_delegation:
            lines.append("  allow_delegation: true")
        if agent.allow_code_execution:
            lines.append("  allow_code_execution: true")
        if agent.max_iter is not None:
            lines.append(f"  max_iter: {agent.max_iter}")
        if agent.multimodal:
            lines.append("  multimodal: true")
        if agent.tools:
            lines.append("  tools:")
            for tool in agent.tools:
                lines.append(f"    - {tool.name}")
        lines.append("")
    return "\n".join(lines)


def _gen_tasks_yaml(tasks: list[TaskModel]) -> str:
    """Generate config/tasks.yaml."""
    if not tasks:
        return "# No tasks defined.\n"
    lines: list[str] = []
    for task in tasks:
        lines.append(f"{task.name}:")
        lines.append(_yaml_str("description", task.description, 1))
        lines.append(_yaml_str("expected_output", task.expected_output, 1))
        if task.agent_role:
            lines.append(f"  agent: {task.agent_role}")
        if task.context:
            lines.append("  context:")
            for ctx in task.context:
                lines.append(f"    - {ctx}")
        if task.output_file:
            lines.append(f'  output_file: "{task.output_file}"')
        if task.human_input:
            lines.append("  human_input: true")
        if task.async_execution:
            lines.append("  async_execution: true")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
#  Tool stub generator
# ═══════════════════════════════════════════════


def _gen_tool_stub(tool_names: list[str]) -> str:
    """Generate tools/custom_tool.py with TODO placeholders."""
    stubs: list[str] = []
    for name in tool_names:
        stubs.append(
            f"class {name}:\n"
            f'    """TODO: Implement the {name} tool."""\n'
            f"    pass\n"
        )
    return (
        f"{CREWAI_VERSION_HEADER}"
        f"# Custom tool stubs — replace with real implementations.\n"
        f"# See: https://docs.crewai.com/core-concepts/Tools/\n"
        f"\n"
        f"{chr(10).join(stubs)}"
    )


def _collect_custom_tools(crew: CrewModel) -> list[str]:
    """Return sorted unique custom-tool names referenced by the crew."""
    names: set[str] = set()
    for agent in crew.agents:
        for tool in agent.tools:
            if tool.kind == "custom":
                names.add(tool.name)
    for task in crew.tasks:
        for tool in task.tools:
            if tool.kind == "custom":
                names.add(tool.name)
    return sorted(names)


# ═══════════════════════════════════════════════
#  String helpers
# ═══════════════════════════════════════════════


def _indent(text: str, level: int) -> str:
    """Indent each non-empty line by *level* × 4 spaces.

    Empty lines are preserved without trailing whitespace.
    """
    prefix = " " * (level * 4)
    return "\n".join(
        prefix + line if line.strip() else "" for line in text.split("\n")
    )


def _escape_str(value: str) -> str:
    """Escape for Python double-quoted f-string literals."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _escape_yaml(value: str) -> str:
    """Escape for YAML double-quoted scalars."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _to_identifier(name: str) -> str:
    """Convert a human-readable name to a valid Python identifier.

    Examples:
        "Senior Researcher" → "senior_researcher"
        "123 crew"          → "_123_crew"
    """
    cleaned = _ID_CLEAN.sub("_", name).strip("_").lower()
    if not cleaned:
        return "crew"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if cleaned in _PYTHON_KEYWORDS:
        cleaned = f"{cleaned}_"
    return cleaned


def _to_class_name(name: str) -> str:
    """Convert a name to PascalCase Python class name."""
    cleaned = _ID_CLEAN.sub(" ", name).strip()
    parts = cleaned.split()
    result = "".join(p.capitalize() for p in parts if p)
    if not result:
        return "Crew"
    if result[0].isdigit():
        result = f"C{result}"
    return result


def _format_input_items(crew: CrewModel) -> str:
    """Format crew inputs as comma-separated key: value pairs."""
    items: list[str] = []
    for inp in crew.inputs:
        val = inp.default
        if isinstance(val, str):
            items.append(f'"{_escape_str(inp.name)}": "{_escape_str(val)}"')
        elif val is None:
            items.append(f'"{_escape_str(inp.name)}": None')
        elif isinstance(val, bool):
            items.append(f'"{_escape_str(inp.name)}": {str(val).lower()}')
        else:
            items.append(f'"{_escape_str(inp.name)}": {val}')
    return ", ".join(items)


def _yaml_str(key: str, value: str, indent: int = 0) -> str:
    """Format a single YAML key-value line.

    Multi-line values use the literal-block scalar (``|``).
    Strings containing ``:`` are double-quoted.
    """
    prefix = "  " * indent
    if "\n" in value:
        block = _indent(value, indent + 1)
        return f"{prefix}{key}: |\n{block}"
    if ":" in value or value.startswith((" ", "-")) or value.endswith(" "):
        return f'{prefix}{key}: "{_escape_yaml(value)}"'
    return f"{prefix}{key}: {value}"
