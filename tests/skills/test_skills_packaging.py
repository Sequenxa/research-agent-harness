from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"


def test_skill_directories_match_frontmatter_name() -> None:
    for skill_dir in sorted(SKILLS.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists(), f"missing SKILL.md in {skill_dir}"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill_md} missing frontmatter"
        name_line = next(
            line for line in text.splitlines()[1:] if line.startswith("name:")
        )
        name = name_line.split(":", 1)[1].strip()
        assert name == skill_dir.name


def test_emit_adapter_stub(tmp_path: Path) -> None:
    script = SKILLS / "research-harness-adapter" / "scripts" / "emit_adapter_stub.py"
    out = tmp_path / "harness.py"
    subprocess.check_call(
        [
            sys.executable,
            str(script),
            "--package",
            "demo",
            "--class-name",
            "DemoRuntime",
            "-o",
            str(out),
        ],
    )
    text = out.read_text(encoding="utf-8")
    assert "class DemoRuntime" in text
    assert "def create_runtime" in text


def test_emit_plan_script(tmp_path: Path) -> None:
    script = SKILLS / "research-harness-plan" / "scripts" / "emit_plan.py"
    schedule = tmp_path / "schedule.csv"
    schedule.write_text("run,arm\n1,A\n2,B\n", encoding="utf-8")
    out = tmp_path / "plan.json"
    subprocess.check_call(
        [
            sys.executable,
            str(script),
            "--planned-units",
            "2",
            "--unit-of-analysis",
            "run",
            "--schedule",
            str(schedule),
            "--freeze",
            "-o",
            str(out),
        ],
    )
    from research_harness.experiment.plan import compute_plan_hash, load_experiment_plan

    plan = load_experiment_plan(out)
    assert plan.planned_units == 2
    assert plan.frozen_before_outcomes is True
    assert plan.plan_hash == compute_plan_hash(plan)


def test_plugin_json_version_matches_pyproject() -> None:
    import tomllib

    plugin = (REPO / "plugin.json").read_text(encoding="utf-8")
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert f'"version": "{pyproject["project"]["version"]}"' in plugin
