"""AST-based CrewAI Python parser for gui-crew.

Parses .py files containing Agent(), Task(), and Crew() constructor
calls into CrewModel instances using Python's built-in ast module.
Designed for classic-style (inline data) CrewAI project files.

Decorator-style (@CrewBase) files are detected but not fully parsed
without accompanying YAML config files; the parser raises a clear
error in that case.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple

from models import AgentModel, CrewModel, TaskModel, ToolRef


# ═══════════════════════════════════════════════
#  Public types
# ═══════════════════════════════════════════════


@dataclass
class ParseError(Exception):
    """Parse error with source location and severity.

    Attributes:
        line: Source line number (1-based).
        message: Human-readable error description.
        severity: ``"error"`` (blocks parsing) or ``"warning"`` (non-blocking).
    """

    line: int
    message: str
    severity: Literal["error", "warning"] = "error"

    def __str__(self) -> str:
        prefix = "WARNING" if self.severity == "warning" else "ERROR"
        return f"[{prefix}] line {self.line}: {self.message}"


class CrewScanResult(NamedTuple):
    """Result of scanning a directory for crew definition files.

    Attributes:
        path: Absolute or relative path to the parsed file.
        crew_name: Name of the crew extracted from the file.
        crew: Fully constructed CrewModel instance.
    """

    path: str
    crew_name: str
    crew: CrewModel


# ═══════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════


def parse_file(content: str, filename: str = "<string>") -> CrewModel:
    """Parse Python source containing CrewAI definitions into a CrewModel.

    Handles classic-style constructor patterns::

        researcher = Agent(role="Researcher", goal="Find info", ...)
        task1 = Task(description="Research", expected_output="...",
                     agent=researcher, context=[...])
        crew = Crew(agents=[researcher], tasks=[task1],
                    process=Process.sequential)

    Symbol references (``agent=researcher``) are resolved through a
    symbol table built from assignment targets.  Variable chaining
    (``a = b = Agent(...)``) is supported through the first target.

    Args:
        content: Python source code to parse.
        filename: Source filename for error messages (default: ``"<string>"``).

    Returns:
        Constructed CrewModel instance with agents, tasks, and crew config.

    Raises:
        ParseError: If required definitions are missing or cannot be resolved.
        SyntaxError: If the source contains invalid Python syntax.
    """
    try:
        tree = ast.parse(content, filename=filename)
    except SyntaxError as e:
        raise ParseError(
            line=e.lineno or 0,
            message=f"Syntax error: {e.msg}",
        ) from e

    visitor = CrewAIVisitor(content, filename)
    visitor.visit(tree)

    # Warnings are informational; they do not block model construction.
    # Errors are surfaced via the build step.
    return visitor.build_crew_model()


def scan_directory(path: Path) -> list[CrewScanResult]:
    """Recursively scan a directory for CrewAI Python files.

    Every ``.py`` file discovered by ``rglob`` is parsed.  Files that
    fail to parse (syntax errors, missing Crew definitions) are silently
    skipped — this is intentional so that non-crew Python files in the
    same tree do not trigger false errors.

    Args:
        path: Root directory to scan.

    Returns:
        List of successfully parsed crews, sorted by file path.
    """
    results: list[CrewScanResult] = []

    for py_file in sorted(path.rglob("*.py")):
        # Skip virtualenv site-packages to avoid parsing third-party code
        parts = py_file.parts
        if ".venv" in parts or "site-packages" in parts:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            crew = parse_file(content, str(py_file))
            results.append(
                CrewScanResult(path=str(py_file), crew_name=crew.name, crew=crew)
            )
        except (ParseError, SyntaxError, ValueError):
            continue  # Not a crew file or unparseable — skip silently.

    return results


# ═══════════════════════════════════════════════
#  AST Visitor
# ═══════════════════════════════════════════════


class CrewAIVisitor(ast.NodeVisitor):
    """AST visitor that extracts CrewAI definitions from Python source.

    Walks the module-level AST collecting ``Agent()``, ``Task()``, and
    ``Crew()`` constructor calls.  A symbol table keyed by variable name
    is built incrementally from assignment targets and used to resolve
    inter-entity references (e.g. ``agent=researcher`` in a Task call).

    Design: ``visit_Assign`` handles top-level calls directly.  Nested
    calls (inside lists, dicts) are left to ``_resolve_expr`` which
    consults the symbol table for already-parsed entities.

    Attributes:
        warnings: Non-blocking issues found during the AST walk.
        errors: Blocking issues that prevent model construction.
    """

    def __init__(self, source: str, filename: str) -> None:
        super().__init__()
        self.source = source
        self.filename = filename

        # symbol_table: var_name → AgentModel | TaskModel | primitive
        self.symbol_table: dict[str, Any] = {}
        # Indexed by role for deduplication and lookup
        self._agent_map: dict[str, AgentModel] = {}
        self._tasks: list[TaskModel] = []
        self._crew_kwargs: dict[str, Any] = {}

        self.warnings: list[ParseError] = []
        self.errors: list[ParseError] = []

    # -- builder ------------------------------------------------------------

    def build_crew_model(self) -> CrewModel:
        """Construct a CrewModel from all data collected during the AST walk.

        Returns:
            Fully validated CrewModel instance.

        Raises:
            ParseError: If errors were recorded during the walk, or if no
                ``Crew()`` call was found.
        """
        if self.errors:
            raise self.errors[0]

        if not self._crew_kwargs:
            raise ParseError(
                line=1,
                message=(
                    "No Crew() constructor call found in file. "
                    "Expected pattern: crew = Crew(agents=[...], tasks=[...], ...)"
                ),
            )

        kwargs = dict(self._crew_kwargs)

        # Honour explicit agent/task lists from Crew(); otherwise include all.
        agent_roles: list[str] = kwargs.pop("_agent_roles", [])
        task_names: list[str] = kwargs.pop("_task_names", [])

        if agent_roles:
            agents = [
                self._agent_map[r] for r in agent_roles if r in self._agent_map
            ]
        else:
            agents = list(self._agent_map.values())

        if task_names:
            task_lookup = {t.name: t for t in self._tasks}
            tasks = [task_lookup[n] for n in task_names if n in task_lookup]
        else:
            tasks = list(self._tasks)

        return CrewModel(agents=agents, tasks=tasks, **kwargs)

    # -- visit hooks --------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        """Capture top-level assignments to constructor calls.

        The first ``ast.Name`` target is used as the variable name.
        Multi-target assignments (``a = b = Agent(...)``) only use
        the first target for symbol-table registration.
        """
        target_name: str | None = None
        for target in node.targets:
            if isinstance(target, ast.Name):
                target_name = target.id
                break

        if target_name is None:
            self.generic_visit(node)
            return

        if isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name):
                if func.id == "Agent":
                    self._handle_agent(target_name, node.value)
                    return
                if func.id == "Task":
                    self._handle_task(target_name, node.value)
                    return
                if func.id == "Crew":
                    self._handle_crew(target_name, node.value)
                    return

        # Store other resolvable assignments for symbol-table lookup.
        try:
            resolved = self._resolve_expr(node.value)
            if resolved is not None:
                self.symbol_table[target_name] = resolved
        except Exception:
            pass  # best-effort — unresolvable expressions are skipped.

        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Detect ``@CrewBase`` decorated classes.

        Decorator-style crews store data in YAML config files; the
        parser cannot extract full models without them.  A clear error
        message with the class line number is raised.
        """
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "CrewBase":
                self.errors.append(
                    ParseError(
                        line=node.lineno,
                        message=(
                            f"@CrewBase decorator on class '{node.name}' detected. "
                            "Decorator-style crews place agent/task data in "
                            "YAML config files which are not available for "
                            "single-file import.  Use classic-style export "
                            "(Agent/Task/Crew constructors) for round-trip "
                            "compatibility."
                        ),
                    )
                )
                return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Warn about aliased imports that may break symbol resolution."""
        for alias in node.names:
            if alias.asname is not None:
                self.warnings.append(
                    ParseError(
                        line=node.lineno,
                        message=(
                            f"Aliased import 'import {alias.name} as "
                            f"{alias.asname}' may prevent resolution of "
                            f"'{alias.name}' references."
                        ),
                        severity="warning",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Warn about aliased from-imports."""
        for alias in node.names:
            if alias.asname is not None:
                module = node.module or "<unknown>"
                self.warnings.append(
                    ParseError(
                        line=node.lineno,
                        message=(
                            f"Aliased import 'from {module} import "
                            f"{alias.name} as {alias.asname}' may prevent "
                            f"symbol resolution."
                        ),
                        severity="warning",
                    )
                )
        self.generic_visit(node)

    # -- expression resolution ----------------------------------------------

    def _resolve_expr(self, node: ast.AST | None) -> Any:
        """Resolve an AST expression to its Python runtime value.

        Supported nodes: constants, variable names (symbol table),
        attribute chains, lists, tuples, dicts, calls (tool refs),
        unary minus for numbers, and binary string concatenation.
        Unresolvable nodes return ``None`` quietly.

        Args:
            node: AST expression node (may be ``None``).

        Returns:
            Resolved Python value or ``None``.
        """
        if node is None:
            return None

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            return self.symbol_table.get(node.id, node.id)

        if isinstance(node, ast.Attribute):
            base = self._resolve_expr(node.value)
            if isinstance(base, str):
                return f"{base}.{node.attr}"
            return node.attr

        if isinstance(node, ast.List):
            return [self._resolve_expr(e) for e in node.elts]

        if isinstance(node, ast.Tuple):
            return tuple(self._resolve_expr(e) for e in node.elts)

        if isinstance(node, ast.Dict):
            result: dict[str, Any] = {}
            for k, v in zip(node.keys, node.values):
                key = self._resolve_expr(k)
                if key is not None:
                    result[str(key)] = self._resolve_expr(v)
            return result

        if isinstance(node, ast.Call):
            return self._resolve_call(node)

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                val = self._resolve_expr(node.operand)
                if isinstance(val, (int, float)):
                    return -val
            return self._resolve_expr(node.operand)

        if isinstance(node, ast.BinOp):
            left = self._resolve_expr(node.left)
            right = self._resolve_expr(node.right)
            if isinstance(left, str) and isinstance(right, str):
                if isinstance(node.op, ast.Add):
                    return left + right

        return None

    def _resolve_call(self, node: ast.Call) -> Any:
        """Resolve a Call expression.

        Tool constructors (``SomeTool()``) are heuristically detected by
        name (ends with or contains "Tool") and returned as tool-ref dicts.
        Other calls are resolved to a dict of keyword arguments.
        """
        func_name: str | None = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name and ("Tool" in func_name):
            return {"kind": "builtin", "name": func_name}

        kwargs: dict[str, Any] = {}
        for kw in node.keywords:
            if kw.arg:
                kwargs[kw.arg] = self._resolve_expr(kw.value)
        return kwargs if kwargs else None

    # -- keyword extraction -------------------------------------------------

    def _extract_kwargs(self, node: ast.Call) -> dict[str, Any]:
        """Extract all named keyword arguments from a Call as a dict."""
        result: dict[str, Any] = {}
        for kw in node.keywords:
            if kw.arg:
                result[kw.arg] = self._resolve_expr(kw.value)
        return result

    # -- handlers -----------------------------------------------------------

    def _handle_agent(self, var_name: str, node: ast.Call) -> None:
        """Collect an Agent definition from ``Agent(...)``."""
        kwargs = self._extract_kwargs(node)

        role_raw = kwargs.get("role", var_name)
        role = str(role_raw).strip() if role_raw is not None else var_name

        goal_raw = kwargs.get("goal")
        if not goal_raw:
            self.errors.append(
                ParseError(
                    line=node.lineno,
                    message=f"Agent '{role}' is missing required 'goal' argument.",
                )
            )
            return

        tool_refs = self._parse_tools(kwargs.get("tools"))

        agent = AgentModel(
            role=role,
            goal=str(goal_raw),
            backstory=str(kwargs.get("backstory", "")),
            tools=tool_refs,
            allow_delegation=bool(kwargs.get("allow_delegation", False)),
            allow_code_execution=bool(kwargs.get("allow_code_execution", False)),
            max_iter=kwargs.get("max_iter"),
            memory=kwargs.get("memory", False),
            multimodal=bool(kwargs.get("multimodal", False)),
        )

        self._agent_map[agent.role] = agent
        self.symbol_table[var_name] = agent

    def _handle_task(self, var_name: str, node: ast.Call) -> None:
        """Collect a Task definition from ``Task(...)``."""
        kwargs = self._extract_kwargs(node)

        # Resolve agent → role string
        agent_value = kwargs.get("agent")
        if isinstance(agent_value, AgentModel):
            agent_role: str | None = agent_value.role
        elif isinstance(agent_value, str):
            agent_role = agent_value
        else:
            agent_role = None

        # Resolve context → list of task names
        context_raw = kwargs.get("context", [])
        context: list[str] = []
        if isinstance(context_raw, list):
            for item in context_raw:
                if isinstance(item, TaskModel):
                    context.append(item.name)
                elif isinstance(item, str):
                    context.append(item)

        task = TaskModel(
            name=var_name,
            description=str(kwargs.get("description", "")),
            expected_output=str(kwargs.get("expected_output", "")),
            agent_role=agent_role,
            context=context,
        )

        self._tasks.append(task)
        self.symbol_table[var_name] = task

    def _handle_crew(self, var_name: str, node: ast.Call) -> None:
        """Collect crew configuration from ``Crew(...)``."""
        kwargs = self._extract_kwargs(node)

        # Resolve agents list → role strings
        agents_raw = kwargs.get("agents", [])
        agent_roles: list[str] = []
        if isinstance(agents_raw, list):
            for a in agents_raw:
                if isinstance(a, AgentModel):
                    agent_roles.append(a.role)
                elif isinstance(a, str):
                    agent_roles.append(a)

        # Resolve tasks list → task names
        tasks_raw = kwargs.get("tasks", [])
        task_names: list[str] = []
        if isinstance(tasks_raw, list):
            for t in tasks_raw:
                if isinstance(t, TaskModel):
                    task_names.append(t.name)
                elif isinstance(t, str):
                    task_names.append(t)

        # Normalise Process.sequential / Process.hierarchical → lower name
        process_raw = kwargs.get("process", "sequential")
        process: str = "sequential"
        if isinstance(process_raw, str):
            cleaned = process_raw.replace("Process.", "").lower()
            if cleaned in ("sequential", "hierarchical"):
                process = cleaned

        self._crew_kwargs = {
            "name": str(kwargs.get("name", var_name)),
            "description": str(kwargs.get("description", "")),
            "process": process,
            "verbose": bool(kwargs.get("verbose", False)),
            "planning": bool(kwargs.get("planning", False)),
            "_agent_roles": agent_roles,
            "_task_names": task_names,
        }

    # -- tool parsing -------------------------------------------------------

    @staticmethod
    def _parse_tools(raw: Any) -> list[ToolRef]:
        """Convert resolved tool references to ToolRef list."""
        if not isinstance(raw, list):
            return []
        refs: list[ToolRef] = []
        for item in raw:
            if isinstance(item, dict) and item:
                if "kind" in item:
                    refs.append(ToolRef(**item))
                else:
                    refs.append(
                        ToolRef(kind="builtin", name=str(item.get("name", "unknown")))
                    )
            elif isinstance(item, str):
                refs.append(ToolRef(kind="builtin", name=item))
        return refs
