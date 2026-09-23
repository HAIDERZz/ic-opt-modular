"""skills/author-process-rule/SKILL.md (T13.9): frontmatter, the worked example in sync with demo_6m, runnable commands.

The worked example MUST stay byte-identical to the packaged demo_6m rule.yaml -- the demo profile is executable
documentation (tests generate all six families under it), so a drifted copy would teach a profile nobody
validated. The skill ships in a public repository: its only layer numbers are demo_6m's invented ones.
"""
from __future__ import annotations

import inspect
import re

import yaml

from ic_opt import blocks
from tests.ic_opt.pcell.test_profile_validation import DEMO_RULE

SKILL = DEMO_RULE.parents[6] / "skills" / "author-process-rule" / "SKILL.md"


def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_exists_with_frontmatter() -> None:
    match = re.match(r"^---\n(.*?)\n---\n", skill_text(), flags=re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    front = yaml.safe_load(match.group(1))
    assert front["name"] == "author-process-rule" and "em.validate_profile" in front["description"]


def test_embedded_demo_profile_is_byte_identical() -> None:
    text = skill_text()
    start = text.index("```yaml\n# demo_6m --") + len("```yaml\n")
    end = text.index("\n```", start)
    assert text[start:end + 1] == DEMO_RULE.read_text(encoding="utf-8"), (
        "SKILL.md's worked example drifted from src/ic_opt/em/pcell/profiles/demo_6m/rule.yaml -- update both together")


def test_skill_commands_are_this_repositorys() -> None:
    text = skill_text()
    assert "ic-opt call em.validate_profile" in text and "IC_OPT_PROFILE_DIRS" in text
    for stale in ("hermes-workflow", "EM_IC_OPT_PROFILE_DIRS", "validate-profile", "--profile-dir", "--generate", "process_data/"):
        assert stale not in text, f"SKILL.md still names {stale!r}"
    params = inspect.signature(blocks.REGISTRY["em.validate_profile"].fn).parameters
    for used in re.findall(r"\b(proc|generate|families|out)=", text):
        assert used in params, f"SKILL.md passes {used}= which em.validate_profile does not take"
    for stage in ("[schema]", "[consistency]", "[emx-names-vs-proc]", "[gds-layers-vs-proc]", "[generation]"):
        assert stage in text


def test_skill_covers_the_load_bearing_content() -> None:
    text = skill_text()
    for required in ("process-rule-profile-v1", "conductor-count mismatch", "coverage.metal_width_space", "passive_via_array_coverage",
                     "Fail-closed", "not_yet_modeled", "define", "profile: <profile_id>", "em.process_file"):
        assert required in text, f"SKILL.md is missing {required!r}"


def test_skill_carries_no_real_process_layer_numbers() -> None:
    demo = yaml.safe_load(DEMO_RULE.read_text(encoding="utf-8"))["layer_catalog"]
    invented = {tuple(r[k]) for group in demo.values() for r in group.values() for k in ("drawing", "pin") if k in r}
    pairs = {(int(a), int(b)) for a, b in re.findall(r"\[(\d+), (\d+)\]", skill_text())}
    assert pairs and pairs <= invented, f"layer pairs outside demo_6m: {sorted(pairs - invented)}"
