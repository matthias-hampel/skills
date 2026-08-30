from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADDER = REPOSITORY_ROOT / "scripts" / "add-skill.py"
UPDATER = REPOSITORY_ROOT / "scripts" / "update-skills.py"

spec = importlib.util.spec_from_file_location("add_skill", ADDER)
assert spec and spec.loader
add_skill_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = add_skill_module
spec.loader.exec_module(add_skill_module)


def run(arguments: list[str], cwd: Path) -> None:
    subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class AddSkillTest(unittest.TestCase):
    def test_parse_github_tree_url(self) -> None:
        source = add_skill_module.parse_github_tree_url(
            "https://github.com/mattpocock/skills/tree/main/skills/engineering/code-review"
        )
        self.assertEqual(source["repository"], "https://github.com/mattpocock/skills.git")
        self.assertEqual(source["ref"], "main")
        self.assertEqual(source["path"], "skills/engineering/code-review")
        self.assertEqual(source["name"], "code-review")

    def test_explicit_ref_with_slashes(self) -> None:
        source = add_skill_module.parse_github_tree_url(
            "https://github.com/owner/repository/tree/feature/review/skills/code-review",
            "feature/review",
        )
        self.assertEqual(source["ref"], "feature/review")
        self.assertEqual(source["path"], "skills/code-review")

    def test_blob_directory_url_is_normalized_to_tree(self) -> None:
        source = add_skill_module.parse_github_tree_url(
            "https://github.com/vercel-labs/agent-skills/blob/main/skills/web-design-guidelines"
        )
        self.assertEqual(source["ref"], "main")
        self.assertEqual(source["path"], "skills/web-design-guidelines")
        self.assertEqual(
            source["url"],
            "https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines",
        )

    def test_adds_validated_directory_and_refuses_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream = root / "upstream"
            skill = upstream / "collection" / "upstream-directory"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: new-skill\ndescription: Test adding a skill.\n---\n\n# New skill\n",
                encoding="utf-8",
            )
            (skill / "references").mkdir()
            (skill / "references" / "rules.md").write_text("rules\n", encoding="utf-8")

            run(["git", "init", "--quiet", "--initial-branch=main"], upstream)
            run(["git", "config", "user.name", "Test"], upstream)
            run(["git", "config", "user.email", "test@example.invalid"], upstream)
            run(["git", "config", "commit.gpgsign", "false"], upstream)
            run(["git", "add", "--all"], upstream)
            run(["git", "commit", "--quiet", "-m", "initial"], upstream)

            skills_dir = root / "library" / "skills"
            destination = add_skill_module.add_skill(
                repository=str(upstream),
                ref="main",
                upstream_path="collection/upstream-directory",
                source_url="https://github.com/example/upstream/tree/main/collection/upstream-directory",
                skills_dir=skills_dir,
                updater=UPDATER,
            )

            self.assertEqual(destination, skills_dir.resolve() / "new-skill")
            self.assertEqual((destination / "references" / "rules.md").read_text(), "rules\n")
            source = (destination / "SOURCE.md").read_text()
            self.assertIn(f"repository: {upstream}", source)
            lock = json.loads((destination / "SOURCE.lock.json").read_text())
            self.assertIn("references/rules.md", lock["files"])

            with self.assertRaisesRegex(add_skill_module.AddError, "skill already exists"):
                add_skill_module.add_skill(
                    repository=str(upstream),
                    ref="main",
                    upstream_path="collection/upstream-directory",
                    source_url="https://github.com/example/upstream/tree/main/collection/upstream-directory",
                    skills_dir=skills_dir,
                    updater=UPDATER,
                )


if __name__ == "__main__":
    unittest.main()
