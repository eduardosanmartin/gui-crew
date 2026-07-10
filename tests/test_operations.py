"""Unit tests for gui-crew operations — templates, history, import/export, single-task testing."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import models
from operations import (
    BUILTIN_TEMPLATES,
    _diff_highlight,
    _safe_filename,
    _strip_jsonc_comments,
    export_crew_jsonc,
    export_crew_yaml,
    filter_history,
    import_crew_file,
    load_history,
    run_single_task,
    save_run_record,
)


# ═══════════════════════════════════════════════
#  Template Gallery  (Task 2.9)
# ═══════════════════════════════════════════════


class TestBuiltinTemplates:
    """All 5 built-in templates are valid CrewModel instances."""

    def test_five_templates_defined(self):
        assert len(BUILTIN_TEMPLATES) == 5

    def test_template_names(self):
        expected = {
            "Research Crew",
            "Code Review Crew",
            "Content Writer Crew",
            "Data Analysis Crew",
            "Customer Support Crew",
        }
        assert set(BUILTIN_TEMPLATES.keys()) == expected

    def test_every_template_is_crew_model(self):
        for name, crew in BUILTIN_TEMPLATES.items():
            assert isinstance(crew, models.CrewModel), f"{name} is not CrewModel"

    def test_research_crew_structure(self):
        crew = BUILTIN_TEMPLATES["Research Crew"]
        assert crew.process == "sequential"
        assert len(crew.agents) == 2
        assert len(crew.tasks) == 2
        assert crew.agents[0].role == "Researcher"
        assert crew.agents[1].role == "Writer"
        # Two tasks with context dependency
        assert crew.tasks[0].name == "research"
        assert crew.tasks[1].name == "write_report"
        assert "research" in crew.tasks[1].context

    def test_code_review_crew_structure(self):
        crew = BUILTIN_TEMPLATES["Code Review Crew"]
        assert len(crew.agents) == 1
        assert len(crew.tasks) == 1
        assert crew.agents[0].role == "Code Reviewer"
        assert crew.agents[0].allow_code_execution is True

    def test_content_writer_crew_structure(self):
        crew = BUILTIN_TEMPLATES["Content Writer Crew"]
        assert len(crew.agents) == 2
        assert len(crew.tasks) == 2
        assert "write_content" in crew.tasks[1].context

    def test_data_analysis_crew_structure(self):
        crew = BUILTIN_TEMPLATES["Data Analysis Crew"]
        assert len(crew.agents) == 1
        assert crew.agents[0].role == "Data Analyst"
        assert crew.agents[0].allow_code_execution is True

    def test_customer_support_crew_structure(self):
        crew = BUILTIN_TEMPLATES["Customer Support Crew"]
        assert len(crew.agents) == 1
        assert crew.agents[0].role == "Support Agent"
        assert crew.agents[0].memory is True

    def test_template_inputs_defined(self):
        """Templates with {variable} in prompts must have inputs defined."""
        # Research Crew should have 'topic' input
        research = BUILTIN_TEMPLATES["Research Crew"]
        input_names = {v.name for v in research.inputs}
        assert "topic" in input_names

        # Content Writer should have 'topic' and 'audience'
        writer = BUILTIN_TEMPLATES["Content Writer Crew"]
        input_names = {v.name for v in writer.inputs}
        assert "topic" in input_names
        assert "audience" in input_names

    def test_templates_serialize_roundtrip(self):
        """Every template survives JSON round-trip."""
        for name, crew in BUILTIN_TEMPLATES.items():
            json_str = crew.to_crewai_json()
            restored = models.CrewModel.from_crewai_json(json_str)
            assert restored.name == crew.name, f"{name}: name mismatch"
            assert len(restored.agents) == len(crew.agents), f"{name}: agent count"
            assert len(restored.tasks) == len(crew.tasks), f"{name}: task count"


# ═══════════════════════════════════════════════
#  History Persistence  (Task 2.11)
# ═══════════════════════════════════════════════


class TestHistoryPersistence:
    """Save and load RunRecord objects to/from disk."""

    def _make_record(self, crew_name: str = "TestCrew", status: str = "success") -> models.RunRecord:
        return models.RunRecord(
            crew_name=crew_name,
            crew_snapshot={"name": crew_name, "process": "sequential"},
            timestamp=datetime(2025, 7, 9, 12, 0, 0, tzinfo=timezone.utc),
            duration_ms=5000,
            token_usage=models.TokenUsage(
                input_tokens=100,
                output_tokens=200,
                total_tokens=300,
            ),
            cost=0.005,
            status=status,  # type: ignore[arg-type]
            crewai_version="2.0.0",
        )

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        """Record saved to disk can be loaded back."""
        record = self._make_record()
        filepath = save_run_record(record, base_dir=str(tmp_path))

        assert filepath.exists()
        assert filepath.suffix == ".json"

        records = load_history(base_dir=str(tmp_path))
        assert len(records) == 1
        loaded = records[0]
        assert loaded.crew_name == "TestCrew"
        assert loaded.status == "success"
        assert loaded.duration_ms == 5000
        assert loaded.cost == 0.005
        assert loaded.token_usage.input_tokens == 100
        assert loaded.token_usage.total_tokens == 300

    def test_save_multiple_records(self, tmp_path: Path):
        """Multiple records for same crew are saved correctly."""
        r1 = self._make_record(crew_name="Crew A")
        r2 = self._make_record(crew_name="Crew A")
        r3 = self._make_record(crew_name="Crew B")

        save_run_record(r1, base_dir=str(tmp_path))
        save_run_record(r2, base_dir=str(tmp_path))
        save_run_record(r3, base_dir=str(tmp_path))

        records = load_history(base_dir=str(tmp_path))
        assert len(records) == 3

    def test_load_empty_directory(self, tmp_path: Path):
        """Empty directory returns empty list."""
        records = load_history(base_dir=str(tmp_path))
        assert records == []

    def test_load_nonexistent_directory(self):
        """Nonexistent directory returns empty list."""
        records = load_history(base_dir="/nonexistent/path/12345")
        assert records == []

    def test_invalid_json_skipped(self, tmp_path: Path):
        """Malformed JSON files are skipped gracefully."""
        crew_dir = tmp_path / "badcrew"
        crew_dir.mkdir(parents=True)
        (crew_dir / "bad.json").write_text("{invalid json{{")
        # Also create a valid file to ensure other records still load
        valid_record = self._make_record(crew_name="goodcrew")
        save_run_record(valid_record, base_dir=str(tmp_path))

        records = load_history(base_dir=str(tmp_path))
        # Only the valid record should load
        assert len(records) == 1
        assert records[0].crew_name == "goodcrew"

    def test_safe_filename(self):
        """Sanitise crew names for filesystem."""
        assert _safe_filename("My Crew") == "My_Crew"
        assert _safe_filename("Research/Analysis") == "Research_Analysis"
        assert _safe_filename("test-crew_v2") == "test-crew_v2"
        assert _safe_filename("  spaces  ") == "spaces"
        assert _safe_filename("") == "unknown"


# ═══════════════════════════════════════════════
#  History Filtering  (Task 2.12)
# ═══════════════════════════════════════════════


class TestHistoryFiltering:
    """Filter run records by crew name, status, and date range."""

    def _make_records(self) -> list[models.RunRecord]:
        return [
            models.RunRecord(
                crew_name="Research Crew",
                crew_snapshot={},
                timestamp=datetime(2025, 7, 1, tzinfo=timezone.utc),
                duration_ms=1000,
                status="success",
            ),
            models.RunRecord(
                crew_name="Research Crew",
                crew_snapshot={},
                timestamp=datetime(2025, 7, 2, tzinfo=timezone.utc),
                duration_ms=2000,
                status="failed",
                error="Timeout",
            ),
            models.RunRecord(
                crew_name="Code Review Crew",
                crew_snapshot={},
                timestamp=datetime(2025, 7, 3, tzinfo=timezone.utc),
                duration_ms=3000,
                status="success",
            ),
            models.RunRecord(
                crew_name="Code Review Crew",
                crew_snapshot={},
                timestamp=datetime(2025, 7, 4, tzinfo=timezone.utc),
                duration_ms=4000,
                status="cancelled",
            ),
        ]

    def test_no_filters_returns_all(self):
        records = self._make_records()
        result = filter_history(records)
        assert len(result) == 4

    def test_filter_by_crew_name_exact(self):
        records = self._make_records()
        result = filter_history(records, crew_name="Research Crew")
        assert len(result) == 2
        assert all("Research" in r.crew_name for r in result)

    def test_filter_by_crew_name_partial(self):
        records = self._make_records()
        result = filter_history(records, crew_name="Research")
        assert len(result) == 2

    def test_filter_by_crew_name_case_insensitive(self):
        records = self._make_records()
        result = filter_history(records, crew_name="research crew")
        assert len(result) == 2

    def test_filter_by_status(self):
        records = self._make_records()
        result = filter_history(records, status="failed")
        assert len(result) == 1
        assert result[0].status == "failed"

    def test_filter_by_status_no_match(self):
        records = self._make_records()
        result = filter_history(records, status="running")
        assert len(result) == 0

    def test_filter_by_date_from(self):
        records = self._make_records()
        result = filter_history(
            records,
            date_from=datetime(2025, 7, 3, tzinfo=timezone.utc),
        )
        assert len(result) == 2

    def test_filter_by_date_to(self):
        records = self._make_records()
        result = filter_history(
            records,
            date_to=datetime(2025, 7, 1, tzinfo=timezone.utc),
        )
        assert len(result) == 1

    def test_filter_combined(self):
        records = self._make_records()
        result = filter_history(
            records,
            crew_name="Research",
            status="failed",
        )
        assert len(result) == 1
        assert result[0].crew_name == "Research Crew"
        assert result[0].status == "failed"

    def test_filter_preserves_order(self):
        """Filtering should not change relative order."""
        records = self._make_records()
        result = filter_history(records, crew_name="Code Review")
        assert len(result) == 2
        # Should be in original order (newer last, based on timestamp of creation)
        assert result[0].duration_ms == 3000
        assert result[1].duration_ms == 4000


# ═══════════════════════════════════════════════
#  Diff Highlight  (Task 2.13 helper)
# ═══════════════════════════════════════════════


class TestDiffHighlight:
    """Side-by-side comparison helpers."""

    def test_equal_values_no_highlight(self):
        assert _diff_highlight("foo", "foo") == ("", "")

    def test_different_values_highlighted(self):
        left_cls, right_cls = _diff_highlight(100, 200)
        assert "bg-warning" in left_cls
        assert "bg-warning" in right_cls

    def test_none_values_handled(self):
        left_cls, right_cls = _diff_highlight(None, "value")
        assert "bg-warning" in left_cls
        assert "bg-warning" in right_cls

    def test_both_none_equal(self):
        assert _diff_highlight(None, None) == ("", "")


# ═══════════════════════════════════════════════
#  Export  (Task 2.14)
# ═══════════════════════════════════════════════


class TestExport:
    """Export crew to JSONC and YAML formats."""

    @staticmethod
    def _make_crew() -> models.CrewModel:
        return models.CrewModel(
            name="ExportTest",
            description="A crew for export testing",
            process="sequential",
            agents=[
                models.AgentModel(role="E", goal="Export test"),
            ],
            tasks=[
                models.TaskModel(
                    name="t1",
                    description="Test task",
                    expected_output="Result",
                ),
            ],
            verbose=True,
        )

    def test_export_jsonc_returns_valid_json(self):
        crew = self._make_crew()
        result = export_crew_jsonc(crew)
        parsed = json.loads(result)
        assert parsed["name"] == "ExportTest"
        assert parsed["process"] == "sequential"

    def test_export_jsonc_includes_agents(self):
        crew = self._make_crew()
        result = export_crew_jsonc(crew)
        parsed = json.loads(result)
        assert "agents" in parsed
        assert parsed["agents"] == ["E"]

    def test_export_yaml_contains_key_fields(self):
        crew = self._make_crew()
        result = export_crew_yaml(crew)
        assert "ExportTest" in result
        assert "process: sequential" in result

    def test_export_empty_crew(self):
        crew = models.CrewModel(name="Empty")
        result = export_crew_jsonc(crew)
        parsed = json.loads(result)
        assert parsed["name"] == "Empty"
        assert parsed["tasks"] == []


# ═══════════════════════════════════════════════
#  Import  (Task 2.15)
# ═══════════════════════════════════════════════


class TestImport:
    """Import crew from JSONC/YAML files."""

    def _valid_jsonc(self) -> str:
        return json.dumps({
            "name": "ImportTest",
            "description": "Imported crew",
            "process": "sequential",
            "agents": ["Researcher"],
            "agent_config": {
                "Researcher": {
                    "role": "Researcher",
                    "goal": "Research things",
                },
            },
            "tasks": [
                {
                    "name": "t1",
                    "description": "Do research",
                    "expected_output": "Results",
                    "agent": "Researcher",
                },
            ],
        }, indent=2)

    def test_import_jsonc_parses_correctly(self):
        content = self._valid_jsonc()
        crew = import_crew_file(content, "crew.jsonc")
        assert crew.name == "ImportTest"
        assert len(crew.agents) == 1
        assert crew.agents[0].role == "Researcher"
        assert len(crew.tasks) == 1
        assert crew.tasks[0].name == "t1"

    def test_import_json_extension_also_works(self):
        content = self._valid_jsonc()
        crew = import_crew_file(content, "crew.json")
        assert crew.name == "ImportTest"

    def test_import_jsonc_with_comments(self):
        """JSONC files with // comments should parse."""
        content = """{
    // This is a comment
    "name": "CommentedCrew",
    "agents": ["A"],
    "agent_config": {
        "A": {"role": "A", "goal": "G"}
    },
    "tasks": [
        /* block comment */
        {
            "name": "t",             // inline comment
            "description": "d",
            "expected_output": "e"
        }
    ]
}"""
        crew = import_crew_file(content, "crew.jsonc")
        assert crew.name == "CommentedCrew"
        assert len(crew.agents) == 1

    def test_import_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            import_crew_file("not valid json {{", "crew.jsonc")

    def test_import_yaml(self):
        content = """name: YAML Crew
process: sequential
agents:
  - Researcher
agent_config:
  Researcher:
    role: Researcher
    goal: Do research
tasks:
  - name: t1
    description: Test
    expected_output: Result
    agent: Researcher
"""
        crew = import_crew_file(content, "crew.yaml")
        assert crew.name == "YAML Crew"
        assert len(crew.agents) == 1

    def test_import_yml_extension(self):
        content = """name: YML Crew
process: sequential
agents: []
tasks: []
"""
        crew = import_crew_file(content, "crew.yml")
        assert crew.name == "YML Crew"

    def test_import_unsupported_extension_raises(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            import_crew_file("whatever", "crew.xml")

    def test_import_no_extension(self):
        """File without extension defaults to JSONC attempt."""
        # Without extension the ext is empty, and our import tries json/jsonc branch
        with pytest.raises(ValueError, match="Unsupported file format"):
            import_crew_file("{}", "crew")


# ═══════════════════════════════════════════════
#  Import Round-Trip  (Task 2.16)
# ═══════════════════════════════════════════════


class TestImportRoundTrip:
    """Export → Import → assert identical."""

    def _make_full_crew(self) -> models.CrewModel:
        return models.CrewModel(
            schema_version=1,
            name="RoundTrip Crew",
            description="Testing round-trip fidelity",
            process="hierarchical",
            agents=[
                models.AgentModel(
                    role="Manager",
                    goal="Oversee the project",
                    llm=models.LLMModel(model="openai/gpt-4o", temperature=0.3),
                    allow_delegation=True,
                    max_iter=10,
                    tools=[models.ToolRef(kind="builtin", name="SerperDevTool")],
                ),
                models.AgentModel(
                    role="Worker",
                    goal="Execute tasks",
                    allow_code_execution=True,
                    memory=models.MemoryConfig(enabled=True),
                ),
            ],
            tasks=[
                models.TaskModel(
                    name="plan",
                    description="Create a plan",
                    expected_output="Project plan",
                    agent_role="Manager",
                ),
                models.TaskModel(
                    name="execute",
                    description="Execute the plan",
                    expected_output="Results",
                    agent_role="Worker",
                    context=["plan"],
                    guardrails=["no_pii"],
                    markdown=True,
                ),
            ],
            memory=models.MemoryConfig(enabled=True),
            planning=True,
            manager_agent_role="Manager",
            inputs=[
                models.InputVar(name="project", type="str", description="Project name"),
            ],
            verbose=True,
        )

    def test_json_round_trip_preserves_all_fields(self):
        """Export to JSON → import → compare model_dump."""
        original = self._make_full_crew()
        json_str = export_crew_jsonc(original)
        restored = import_crew_file(json_str, "crew.jsonc")

        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.process == original.process
        assert restored.schema_version == original.schema_version
        assert restored.planning == original.planning
        assert restored.verbose == original.verbose

        # Agents
        assert len(restored.agents) == len(original.agents)
        assert restored.agents[0].role == original.agents[0].role
        assert restored.agents[0].goal == original.agents[0].goal
        assert restored.agents[0].allow_delegation == original.agents[0].allow_delegation
        assert restored.agents[0].max_iter == original.agents[0].max_iter
        assert len(restored.agents[0].tools) == len(original.agents[0].tools)

        # Tasks
        assert len(restored.tasks) == len(original.tasks)
        assert restored.tasks[1].context == original.tasks[1].context
        assert restored.tasks[1].guardrails == original.tasks[1].guardrails
        assert restored.tasks[1].markdown == original.tasks[1].markdown

    def test_yaml_round_trip_preserves_core_fields(self):
        """Export to YAML → import → name and structure match."""
        original = self._make_full_crew()
        yaml_str = export_crew_yaml(original)
        restored = import_crew_file(yaml_str, "crew.yaml")

        assert restored.name == original.name
        assert len(restored.agents) == len(original.agents)
        assert len(restored.tasks) == len(original.tasks)
        assert restored.process == original.process

    def test_minimal_crew_round_trip(self):
        """Minimal crew survives round-trip."""
        original = models.CrewModel(
            name="Minimal",
            agents=[models.AgentModel(role="A", goal="G")],
            tasks=[models.TaskModel(name="t", description="d", expected_output="e")],
        )
        json_str = export_crew_jsonc(original)
        restored = import_crew_file(json_str, "crew.jsonc")
        assert restored.name == "Minimal"
        assert len(restored.agents) == 1
        assert len(restored.tasks) == 1

    def test_crew_with_extra_fields_round_trip(self):
        """Extra fields via model_extra survive round-trip."""
        original = models.CrewModel(
            name="Extra",
            agents=[models.AgentModel(role="A", goal="G")],
            tasks=[models.TaskModel(name="t", description="d", expected_output="e")],
            full_output=True,  # unknown → model_extra
        )
        json_str = export_crew_jsonc(original)
        restored = import_crew_file(json_str, "crew.jsonc")
        assert restored.model_extra.get("full_output") is True or restored.full_output is True  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════
#  JSONC Comment Stripping
# ═══════════════════════════════════════════════


class TestJsoncCommentStripping:
    """JSONC comment removal."""

    def test_strip_line_comments(self):
        content = """{
    // line comment
    "key": "value"
}"""
        result = _strip_jsonc_comments(content)
        assert "// line comment" not in result
        assert '"key": "value"' in result

    def test_strip_block_comments(self):
        content = """{
    /* block comment */
    "key": "value"
}"""
        result = _strip_jsonc_comments(content)
        assert "/* block comment */" not in result
        assert '"key": "value"' in result

    def test_strip_multiline_block_comments(self):
        content = """{
    /*
     * multiline
     * comment
     */
    "key": "value"
}"""
        result = _strip_jsonc_comments(content)
        assert "/*" not in result
        assert "*/" not in result
        assert '"key": "value"' in result

    def test_preserve_urls_with_double_slash(self):
        """URLs containing // should not be stripped."""
        content = """{
    "url": "https://example.com/path",
    "key": "value"
}"""
        result = _strip_jsonc_comments(content)
        assert '"url": "https://example.com/path"' in result

    def test_preserve_regex_like_comments_in_strings(self):
        """// inside strings should be preserved."""
        content = '''{
    "pattern": "//this is not a comment",
    "key": "value"
}'''
        result = _strip_jsonc_comments(content)
        assert '"//this is not a comment"' in result

    def test_clean_json_unchanged(self):
        """Clean JSON without comments should be unchanged in structure."""
        content = '{"name": "test", "value": 42}'
        result = _strip_jsonc_comments(content)
        parsed = json.loads(result)
        assert parsed["name"] == "test"
        assert parsed["value"] == 42


# ═══════════════════════════════════════════════
#  Single-Task Testing  (Task 2.17)
# ═══════════════════════════════════════════════


class TestSingleTask:
    """Single-task test execution."""

    def test_task_not_found_raises(self):
        crew = models.CrewModel(
            name="TestCrew",
            tasks=[models.TaskModel(name="t1", description="d", expected_output="e")],
        )
        with pytest.raises(ValueError, match="No task with name"):
            run_single_task(crew, "nonexistent", "mock context")

    def test_returns_fase2_placeholder(self):
        """CrewEngine.test_task() currently returns a Fase 2 placeholder."""
        crew = models.CrewModel(
            name="TestCrew",
            tasks=[models.TaskModel(name="t1", description="d", expected_output="e")],
        )
        result = run_single_task(crew, "t1", "mock context data")
        assert "Fase 2" in result
        assert "t1" in result

    def test_accepts_empty_context(self):
        crew = models.CrewModel(
            name="TestCrew",
            tasks=[models.TaskModel(name="t1", description="d", expected_output="e")],
        )
        result = run_single_task(crew, "t1", "")
        assert "Fase 2" in result


# ═══════════════════════════════════════════════
#  Edge Cases & Error Handling
# ═══════════════════════════════════════════════


class TestEdgeCases:
    """Unusual but valid scenarios."""

    def test_export_crew_with_special_chars_in_name(self):
        crew = models.CrewModel(name="My Crew: Research & Analysis!")
        result = export_crew_jsonc(crew)
        parsed = json.loads(result)
        assert parsed["name"] == "My Crew: Research & Analysis!"

    def test_safe_filename_with_special_chars(self):
        assert _safe_filename("My Crew: Research & Analysis!") == "My_Crew__Research___Analysis_"

    def test_import_with_bom_header(self):
        """Files with UTF-8 BOM should still parse."""
        content = '\ufeff{"name": "BOM Crew", "agents": [], "tasks": []}'
        crew = import_crew_file(content, "crew.jsonc")
        assert crew.name == "BOM Crew"

    def test_history_with_empty_crew_name(self):
        """Saving a record with empty crew name should use 'unknown' dir."""
        record = models.RunRecord(
            crew_name="",
            crew_snapshot={},
            timestamp=datetime.now(timezone.utc),
            status="success",
        )
        # Should not crash
        filepath = save_run_record(record)
        assert filepath.name.endswith(".json")
        # Cleanup
        filepath.unlink()
        filepath.parent.rmdir()

    def test_load_history_sorted_newest_first(self, tmp_path: Path):
        """History records should be sorted newest first."""
        r1 = models.RunRecord(
            crew_name="C",
            crew_snapshot={},
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            status="success",
        )
        r2 = models.RunRecord(
            crew_name="C",
            crew_snapshot={},
            timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
            status="success",
        )
        save_run_record(r1, base_dir=str(tmp_path))
        save_run_record(r2, base_dir=str(tmp_path))

        records = load_history(base_dir=str(tmp_path))
        assert len(records) == 2
        # Newer file has higher mtime, so it should appear first
        assert records[0].timestamp >= records[1].timestamp
