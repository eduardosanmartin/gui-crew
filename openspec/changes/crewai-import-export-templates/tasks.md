# Tasks: CrewAI Python Import/Export, Structural Templates & Examples

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Changed lines | 1550–2300 |
| Budget risk | High |
| Chained PRs | Yes |
| Split | PR 1 → PR 2 → PR 3 |
| Strategy | ask-on-risk |
| Chain | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Base |
|------|------|----|------|
| 1 | Parser + Generator + models.py hooks | PR 1 | main |
| 2 | Templates + Examples + operations.py + app.py | PR 2 | PR 1 branch |
| 3 | All tests + polish | PR 3 | PR 2 branch |

## Phase 1: Core Modules

- [x] 1.1 Create `crewai_python_parser.py`. AST NodeVisitor, symbol table, `parse_file`, `scan_directory`, `ParseError`. AC: `.py` → `CrewModel`, dir scan lists crews, errors show line numbers.
- [x] 1.2 Create `crewai_code_generator.py`. `generate_zip`, f-string helpers, YAML/tool stub builders, version header. AC: decorator default ZIP, classic toggle, includes YAML + tool stubs.
- [ ] 1.3 Create `crewai_templates.py`. `STRUCTURAL_TEMPLATES` dict: 8 skeleton `CrewModel` patterns with `TODO:` placeholders. AC: not runnable as-is.
- [ ] 1.4 Create `crewai_examples.py`. `BUILTIN_EXAMPLES` dict: 5 reclassified + 2 complex crews. AC: reclassified data unchanged, complex crews have 4+ agents/tasks.

## Phase 2: Integration

- [x] 2.1 Modify `models.py`. Add `from_crewai_python` and `to_crewai_python` delegating to parser/generator. AC: round-trip preserves roles, goals, backstories, descriptions, context DAG.
- [ ] 2.2 Modify `operations.py`. Import new modules; add Structural Templates + Examples accordions; extend import for `.py`; add ZIP export dialog with toggles. AC: `.py` loads Builder, ZIP dialog downloads.
- [ ] 2.3 Modify `app.py`. Accept `.py` upload extension. AC: file picker shows `.py`, supports single file and directory.

## Phase 3: Tests

- [ ] 3.1 Create `tests/test_python_parser.py`. Visitor, symbol table, aliased import warnings, syntax error line numbers, directory scan. AC: inline `.py` strings → asserted `CrewModel` or `ParseError`.
- [ ] 3.2 Create `tests/test_code_generator.py`. Decorator vs classic, YAML inclusion, tool stubs, ZIP contents. AC: inspect strings, open `BytesIO` ZIP, assert contents.
- [ ] 3.3 Create `tests/test_templates.py`. 8 patterns, TODO markers, agent/task counts. AC: iterate dict, search `TODO:`, verify counts.
- [ ] 3.4 Create `tests/test_examples.py`. 7 crews, reclassified identity, complex topology. AC: compare dumps, assert structure.
- [ ] 3.5 Modify `tests/test_operations.py`. Update for `BUILTIN_EXAMPLES` rename, new accordions, `.py` import, ZIP export. AC: existing tests pass + new flows covered.

## Phase 4: Polish

- [ ] 4.1 Add "View in Canvas" / "Customize in Builder" shortcut buttons after load. AC: buttons appear and navigate correctly.
- [ ] 4.2 Add export progress feedback (spinner/label) for large crews. AC: visual feedback during generation, dismisses on completion.
- [ ] 4.3 Update README + inline comments documenting round-trip fidelity. AC: README lists preserved/lossy fields; generator docstring enumerates omissions.
