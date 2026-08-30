from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LINKER = REPOSITORY_ROOT / "scripts" / "link-skills.py"

spec = importlib.util.spec_from_file_location("link_skills", LINKER)
assert spec and spec.loader
link_skills_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = link_skills_module
spec.loader.exec_module(link_skills_module)


class LinkSkillsTest(unittest.TestCase):
    def make_skill(self, source_dir: Path, name: str) -> Path:
        skill = source_dir / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test skill.\n---\n",
            encoding="utf-8",
        )
        return skill

    def test_links_missing_skills_and_backs_up_existing_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "library"
            target_dir = root / "agents" / "skills"
            source_dir.mkdir()
            first = self.make_skill(source_dir, "first")
            second = self.make_skill(source_dir, "second")

            old_first = target_dir / "first"
            old_first.mkdir(parents=True)
            (old_first / "old.txt").write_text("old\n", encoding="utf-8")
            unrelated = target_dir / "unrelated"
            unrelated.mkdir()

            backup_dir = link_skills_module.link_skills(source_dir, target_dir)

            self.assertIsNotNone(backup_dir)
            assert backup_dir is not None
            self.assertTrue((backup_dir / "first" / "old.txt").is_file())
            self.assertTrue((target_dir / "first").is_symlink())
            self.assertTrue((target_dir / "second").is_symlink())
            self.assertEqual((target_dir / "first").resolve(), first.resolve())
            self.assertEqual((target_dir / "second").resolve(), second.resolve())
            self.assertTrue(unrelated.is_dir())

            second_run = link_skills_module.link_skills(source_dir, target_dir)
            self.assertIsNone(second_run)

    def test_dry_run_does_not_change_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "library"
            target_dir = root / "agents" / "skills"
            source_dir.mkdir()
            self.make_skill(source_dir, "first")
            existing = target_dir / "first"
            existing.mkdir(parents=True)

            output = io.StringIO()
            with redirect_stdout(output):
                backup_dir = link_skills_module.link_skills(source_dir, target_dir, dry_run=True)

            self.assertIsNotNone(backup_dir)
            self.assertTrue(existing.is_dir())
            self.assertFalse(existing.is_symlink())
            assert backup_dir is not None
            self.assertFalse(backup_dir.exists())
            self.assertIn("Dry run", output.getvalue())


if __name__ == "__main__":
    unittest.main()
