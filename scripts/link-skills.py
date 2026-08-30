#!/usr/bin/env python3
"""Link repository skills into an agent skill directory."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys


class LinkError(RuntimeError):
    pass


def exists_or_link(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def points_to(link: Path, source: Path) -> bool:
    return link.is_symlink() and link.resolve(strict=False) == source.resolve()


def contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def discover_skills(source_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in source_dir.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )


def new_backup_dir(target_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return target_dir.parent / f"{target_dir.name}-backup-{timestamp}"


def link_skills(source_dir: Path, target_dir: Path, dry_run: bool = False) -> Path | None:
    source_dir = source_dir.expanduser().resolve()
    target_dir = target_dir.expanduser().absolute()

    if not source_dir.is_dir():
        raise LinkError(f"{source_dir}: source directory does not exist")
    if target_dir.is_symlink():
        if target_dir.resolve(strict=False) == source_dir:
            print(f"{target_dir} already links to {source_dir}")
            return None
        raise LinkError(f"{target_dir}: target directory is itself a symlink")
    if target_dir.exists() and not target_dir.is_dir():
        raise LinkError(f"{target_dir}: target is not a directory")

    resolved_target = target_dir.resolve(strict=False)
    if source_dir == resolved_target or contains(source_dir, resolved_target) or contains(resolved_target, source_dir):
        raise LinkError("source and target directories must not contain each other")

    skills = discover_skills(source_dir)
    if not skills:
        raise LinkError(f"{source_dir}: no skill directories found")

    missing: list[tuple[Path, Path]] = []
    replacements: list[tuple[Path, Path]] = []
    unchanged = 0
    for source in skills:
        target = target_dir / source.name
        if points_to(target, source):
            unchanged += 1
        elif exists_or_link(target):
            replacements.append((source, target))
        else:
            missing.append((source, target))

    backup_dir = new_backup_dir(target_dir) if replacements else None
    for source, target in replacements:
        assert backup_dir is not None
        print(f"backup {target} -> {backup_dir / target.name}")
        print(f"link   {target} -> {source}")
    for source, target in missing:
        print(f"link   {target} -> {source}")

    if dry_run:
        print(
            f"Dry run: {len(replacements)} backups, "
            f"{len(replacements) + len(missing)} links, {unchanged} unchanged"
        )
        return backup_dir

    moved: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if backup_dir is not None:
            backup_dir.mkdir(parents=True)
            for _, target in replacements:
                backup = backup_dir / target.name
                target.rename(backup)
                moved.append((backup, target))

        for source, target in [*replacements, *missing]:
            target.symlink_to(source, target_is_directory=True)
            created.append(target)
    except OSError as exc:
        for target in reversed(created):
            if target.is_symlink():
                target.unlink()
        for backup, target in reversed(moved):
            if backup.exists() or backup.is_symlink():
                backup.rename(target)
        if backup_dir is not None:
            try:
                backup_dir.rmdir()
            except OSError:
                pass
        raise LinkError(f"linking failed and changes were rolled back: {exc}") from exc

    print(
        f"Linked {len(replacements) + len(missing)} skills; "
        f"{unchanged} already linked"
    )
    if backup_dir is not None:
        print(f"Previous entries: {backup_dir}")
    return backup_dir


def build_parser() -> argparse.ArgumentParser:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=repository_root / "skills",
        help="skill library directory (default: repository skills directory)",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.home() / ".agents" / "skills",
        help="agent skill directory (default: ~/.agents/skills)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        link_skills(arguments.source_dir, arguments.target_dir, arguments.dry_run)
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
