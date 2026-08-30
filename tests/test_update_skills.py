from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UPDATER = REPOSITORY_ROOT / "scripts" / "update-skills.py"


def run(arguments: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class UpdateSkillsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        run(["git", "init", "--quiet", "--initial-branch=main"], self.upstream)
        run(["git", "config", "user.name", "Test"], self.upstream)
        run(["git", "config", "user.email", "test@example.invalid"], self.upstream)
        run(["git", "config", "commit.gpgsign", "false"], self.upstream)

        self.upstream_skill = self.upstream / "collection" / "example"
        self.upstream_skill.mkdir(parents=True)
        self.write_upstream("First version\n")
        (self.upstream_skill / "references").mkdir()
        (self.upstream_skill / "references" / "notes.md").write_text("notes\n", encoding="utf-8")
        self.commit_upstream("initial")

        self.skills_dir = self.root / "library" / "skills"
        self.skill = self.skills_dir / "example"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(self.skill_text("Local old version\n"), encoding="utf-8")
        (self.skill / "personal.txt").write_text("keep me\n", encoding="utf-8")
        (self.skill / "SOURCE.md").write_text(
            "\n".join(
                [
                    "---",
                    f"repository: {self.upstream}",
                    "ref: main",
                    "path: collection/example",
                    "---",
                    "",
                    "Local test source",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def skill_text(body: str) -> str:
        return (
            "---\n"
            "name: example\n"
            "description: Test skill.\n"
            "metadata:\n"
            "  author: Test\n"
            "---\n\n"
            "# Example\n\n"
            + body
        )

    def write_upstream(self, body: str) -> None:
        (self.upstream_skill / "SKILL.md").write_text(self.skill_text(body), encoding="utf-8")

    def commit_upstream(self, message: str) -> None:
        run(["git", "add", "--all"], self.upstream)
        run(["git", "commit", "--quiet", "-m", message], self.upstream)

    def update(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run(
            [
                sys.executable,
                str(UPDATER),
                "--skills-dir",
                str(self.skills_dir),
                *arguments,
            ],
            self.root,
            check=check,
        )

    def test_directory_update_adds_and_removes_managed_files(self) -> None:
        first = self.update("--all")
        self.assertIn("example: updated", first.stdout)
        self.assertEqual((self.skill / "references" / "notes.md").read_text(), "notes\n")
        self.assertEqual((self.skill / "personal.txt").read_text(), "keep me\n")

        lock = json.loads((self.skill / "SOURCE.lock.json").read_text())
        self.assertEqual(lock["path"], "collection/example")
        self.assertIn("references/notes.md", lock["files"])

        (self.upstream_skill / "references" / "notes.md").unlink()
        script = self.upstream_skill / "scripts" / "helper.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.write_upstream("Second version\n")
        self.commit_upstream("update skill")

        second = self.update("--all")
        self.assertIn("remove references/notes.md", second.stdout)
        self.assertFalse((self.skill / "references").exists())
        self.assertTrue((self.skill / "scripts" / "helper.sh").stat().st_mode & stat.S_IXUSR)
        self.assertEqual((self.skill / "personal.txt").read_text(), "keep me\n")

        current = self.update("--all")
        self.assertIn("example: up to date", current.stdout)

    def test_local_change_causes_conflict(self) -> None:
        self.update("--all")
        (self.skill / "SKILL.md").write_text(self.skill_text("Personal edit\n"), encoding="utf-8")
        self.write_upstream("Upstream edit\n")
        self.commit_upstream("change same file")

        result = self.update("--all", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("locally modified managed files: SKILL.md", result.stderr)
        self.assertIn("Personal edit", (self.skill / "SKILL.md").read_text())

    def test_check_reports_updates_without_writing(self) -> None:
        result = self.update("--all", "--check", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("example: update available", result.stdout)
        self.assertFalse((self.skill / "SOURCE.lock.json").exists())
        self.assertIn("Local old version", (self.skill / "SKILL.md").read_text())


if __name__ == "__main__":
    unittest.main()
