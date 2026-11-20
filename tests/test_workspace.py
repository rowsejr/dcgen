"""VS Code / Git workflow: project folders, YAML checks with file:line:col, runs and summaries, schemas,
and the editor configuration (tasks, Copilot instructions and skills). Nothing here uses AI."""
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from dcgen import workspace as ws

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def proj(tmp_path):
    ws.new_project("t1", tmp_path)
    return tmp_path / "projects" / "t1"


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


# --------------------------------------------------------------- project folders
def test_new_project_is_schema_linked_and_safe(tmp_path, proj):
    b = (proj / "brief.yaml").read_text()
    assert b.startswith("# yaml-language-server: $schema=../../schemas/brief.schema.json") and 'project: "t1"' in b
    assert (proj / "site.yaml").read_text().startswith("# yaml-language-server: $schema=../../schemas/site.schema.json")
    assert (proj / "README.md").exists()
    with pytest.raises(FileExistsError):
        ws.new_project("t1", tmp_path)
    with pytest.raises(ValueError):
        ws.new_project("../escape", tmp_path)


def test_example_project_checks_clean_with_engine_feedback(proj):
    issues = ws.check(proj / "brief.yaml")
    assert not _errors(issues)
    assert any(i.severity == "info" and "peak PUE" in i.message for i in issues)


def test_typos_and_bad_values_point_at_the_line(proj):
    p = proj / "brief.yaml"
    p.write_text(p.read_text() + "it_load: 30\nchw_flow_c: warm\n")
    lines = p.read_text().splitlines()
    issues = ws.check(proj)
    typo = next(i for i in issues if "it_load'" in i.message)
    assert typo.severity == "error" and "did you mean 'it_load_mw'" in typo.message and typo.line == lines.index("it_load: 30") + 1
    bad = next(i for i in issues if i.message.startswith("chw_flow_c"))
    assert bad.severity == "error" and bad.line == lines.index("chw_flow_c: warm") + 1     # the last duplicate wins
    assert any(i.severity == "warning" and "duplicate key 'chw_flow_c'" in i.message for i in issues)


def test_override_and_unit_mistakes_are_caught(proj):
    p = proj / "brief.yaml"
    base = p.read_text()
    p.write_text(base + "overrides:\n  halls.corridoor_depth: 5\n")
    assert any(i.severity == "error" and i.line == len(base.splitlines()) + 1 for i in ws.check(proj))
    p.write_text(base + "hr_unit: acc_fc_1500\n")
    e = _errors(ws.check(proj))
    assert e and "did you mean 'acc_fc_1400'" in e[0].message


def test_site_geometry_checks(proj):
    s = proj / "site.yaml"
    d = yaml.safe_load(s.read_text())
    d["redline"] = [[0, 0], [300, 300], [300, 0], [0, 300]]           # bow tie
    s.write_text(yaml.safe_dump(d))
    assert any("crosses itself" in i.message for i in _errors(ws.check(proj)))
    d["redline"] = [[0, 0], [300, 0], [300, 300], [0, 300]]
    d["receptors"] = [{"name": "inside", "x": 150, "y": 150, "limit_dba": 45}]
    d["access_points"] = [[150, 500]]
    s.write_text(yaml.safe_dump(d))
    msgs = [i.message for i in ws.check(proj) if i.severity == "warning"]
    assert any("inside the redline" in m for m in msgs) and any("from the redline" in m for m in msgs)
    d.pop("redline")
    d["redline_file"] = "missing.dxf"
    s.write_text(yaml.safe_dump(d))
    assert any("not found" in i.message for i in _errors(ws.check(proj)))


def test_issue_lines_match_the_vscode_problem_matcher():
    tasks = json.loads((REPO / ".vscode" / "tasks.json").read_text())
    check = next(t for t in tasks["tasks"] if t["label"] == "dcgen: check this project")
    rx = re.compile(check["problemMatcher"][0]["pattern"]["regexp"])
    for path in (Path("/home/u/dcgen/projects/a/brief.yaml"), Path(r"C:\Users\u\dcgen\projects\a\brief.yaml")):
        m = rx.match(str(ws.Issue(path, 12, 3, "error", "it_load: something: odd")))
        assert m and m.group(1) == str(path) and m.group(2) == "12" and m.group(4) == "error"
        assert m.group(5) == "it_load: something: odd"


# --------------------------------------------------------------- runs and summaries
@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws")
    ws.new_project("r1", root)
    d = root / "projects" / "r1"
    first = ws.run(d / "site.yaml")
    b = d / "brief.yaml"
    b.write_text(b.read_text().replace("chw_flow_c: 20", "chw_flow_c: 26"))
    second = ws.run(d)
    return d, first, second


def test_run_writes_summary_for_the_next_edit(ran):
    d, first, second = ran
    assert (d / "out" / "summary.json").exists() and (d / "out" / "summary.md").exists()
    assert first["fits"] and first["metrics"]["peak_pue"] == pytest.approx(1.48, abs=0.005)
    md = (d / "out" / "summary.md").read_text()
    for heading in ("## Result", "## Options", "## Changes since the last run", "no AI involved"):
        assert heading in md
    assert "peak_pue" in second["changes"] and second["changes"]["peak_pue"][1] < second["changes"]["peak_pue"][0]


def test_run_problems_anchor_to_brief(ran):
    d, first, _ = ran
    probs = ws.problems(first, d)
    assert probs and all(p.file == d / "brief.yaml" and p.severity in ("error", "warning") for p in probs)


# --------------------------------------------------------------- schemas and editor configuration
def test_committed_schemas_are_current(tmp_path):
    for f in ws.write_schemas(tmp_path):
        assert f.read_text() == (REPO / "schemas" / f.name).read_text(), f"{f.name}: run `dcgen project schema`"


def test_examples_and_mappings_validate_against_the_schemas():
    jsonschema = pytest.importorskip("jsonschema")
    pairs = {"brief": ["examples/brief.yaml"], "site": ["examples/site.yaml"],
             "mapping": sorted(str(p.relative_to(REPO)) for p in (REPO / "packs").glob("*/mappings/*.yaml")),
             "profile": ["examples/pack/clients/exampleco/profile.yaml"]}
    for name, files in pairs.items():
        v = jsonschema.Draft202012Validator(json.loads((REPO / "schemas" / f"{name}.schema.json").read_text()))
        for f in files:
            assert not list(v.iter_errors(yaml.safe_load((REPO / f).read_text()))), f


def test_vscode_tasks_call_real_cli_commands():
    from dcgen.cli import build_parser
    tasks = json.loads((REPO / ".vscode" / "tasks.json").read_text())
    parser = build_parser()
    for t in tasks["tasks"]:
        assert t["command"] == "${command:python.interpreterPath}", t["label"]
        if t["args"][:2] == ["-m", "dcgen.cli"]:
            args = [re.sub(r"\$\{[^}]+\}", "x", a) for a in t["args"][2:]]
            parser.parse_args(args)                                  # raises SystemExit if a command or flag is wrong
    settings = json.loads((REPO / ".vscode" / "settings.json").read_text())
    for schema in settings["yaml.schemas"]:
        assert (REPO / schema).exists(), schema


def test_copilot_skills_and_instructions_are_well_formed():
    gh = REPO / ".github"
    assert (gh / "copilot-instructions.md").read_text().startswith("# dcgen")
    for skill in (gh / "skills").glob("*/SKILL.md"):
        front = yaml.safe_load(skill.read_text().split("---")[1])
        assert front["name"] == skill.parent.name and re.fullmatch(r"[a-z0-9-]{1,64}", front["name"])
        assert 20 < len(front["description"]) <= 1024
    for inst in (gh / "instructions").glob("*.instructions.md"):
        assert "applyTo" in yaml.safe_load(inst.read_text().split("---")[1])
