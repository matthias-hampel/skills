#!/usr/bin/env python3
"""Update vendored skills from the upstream locations in SOURCE.md files."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


SOURCE_FILE = "SOURCE.md"
LOCK_FILE = "SOURCE.lock.json"
RESERVED_FILES = {SOURCE_FILE, LOCK_FILE}
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class UpdateError(RuntimeError):
    pass


def parse_frontmatter(text: str, source_path: Path) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise UpdateError(f"{source_path}: missing closing '---'") from exc

    values: dict[str, str] = {}
    last_key: str | None = None
    for line_number, raw_line in enumerate(lines[1:end], 2):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line[:1].isspace():
            if last_key is None:
                raise UpdateError(f"{source_path}:{line_number}: unexpected indentation")
            values[last_key] = f"{values[last_key]}\n{line}"
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise UpdateError(f"{source_path}:{line_number}: expected 'key: value'")
        last_key = key.strip()
        values[last_key] = value.strip().strip("'\"")
    return values


def parse_source(source_path: Path) -> dict[str, str]:
    text = source_path.read_text(encoding="utf-8")
    values = parse_frontmatter(text, source_path)
    if values is None:
        urls = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if len(urls) != 1 or not urls[0].startswith(("https://", "http://")):
            raise UpdateError(
                f"{source_path}: legacy sources must contain one HTTP URL; "
                "use frontmatter for a complete skill directory"
            )
        return {"mode": "file", "url": urls[0]}

    allowed = {"repository", "ref", "path"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise UpdateError(f"{source_path}: unsupported fields: {', '.join(unknown)}")

    missing = sorted(allowed - set(values))
    if missing:
        raise UpdateError(f"{source_path}: missing fields: {', '.join(missing)}")

    if not REF_RE.fullmatch(values["ref"]) or ".." in values["ref"].split("/"):
        raise UpdateError(f"{source_path}: unsafe Git ref {values['ref']!r}")

    upstream_path = PurePosixPath(values["path"])
    if upstream_path.is_absolute() or ".." in upstream_path.parts or str(upstream_path) in {"", "."}:
        raise UpdateError(f"{source_path}: path must be a relative skill directory")

    return {"mode": "directory", **values}


def run_git(arguments: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise UpdateError("git is required for directory sources") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or "git command failed"
        raise UpdateError(detail) from exc
    return result.stdout.strip()


def fetch_directory(source: dict[str, str], temporary_root: Path) -> tuple[Path, str]:
    checkout = temporary_root / "checkout"
    checkout.mkdir()
    run_git(["init", "--quiet"], checkout)
    run_git(["remote", "add", "origin", source["repository"]], checkout)
    run_git(["fetch", "--quiet", "--depth=1", "origin", source["ref"]], checkout)
    run_git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], checkout)

    upstream = checkout.joinpath(*PurePosixPath(source["path"]).parts)
    if not upstream.is_dir():
        raise UpdateError(
            f"{source['path']!r} is not a directory at {source['repository']} {source['ref']}"
        )
    return upstream, run_git(["rev-parse", "HEAD"], checkout)


def fetch_file(source: dict[str, str], temporary_root: Path) -> tuple[Path, str]:
    target = temporary_root / "file-source"
    target.mkdir()
    request = urllib.request.Request(source["url"], headers={"User-Agent": "personal-skill-updater/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UpdateError(f"could not download {source['url']}: {exc}") from exc
    if not content:
        raise UpdateError(f"downloaded an empty file from {source['url']}")
    (target / "SKILL.md").write_bytes(content)
    return target, hashlib.sha256(content).hexdigest()


def validate_skill(skill_dir: Path, expected_name: str | None) -> None:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        raise UpdateError(f"{skill_dir}: upstream directory has no SKILL.md")

    text = skill_file.read_text(encoding="utf-8")
    values = parse_frontmatter(text, skill_file)
    if values is None:
        raise UpdateError(f"{skill_file}: SKILL.md must start with YAML frontmatter")

    name = values.get("name", "")
    description = values.get("description", "")
    if not NAME_RE.fullmatch(name):
        raise UpdateError(f"{skill_file}: invalid or missing skill name")
    if expected_name is not None and name != expected_name:
        raise UpdateError(f"{skill_file}: skill name {name!r} does not match {expected_name!r}")
    if not description:
        raise UpdateError(f"{skill_file}: missing skill description")


def fingerprint(path: Path) -> dict[str, object] | None:
    if not path.is_file() or path.is_symlink():
        return None
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "executable": bool(path.stat().st_mode & 0o111),
    }


def snapshot(directory: Path) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise UpdateError(f"{path}: symbolic links are not supported in imported skills")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative in RESERVED_FILES or relative.startswith(".git/"):
            continue
        value = fingerprint(path)
        assert value is not None
        files[relative] = value
    return files


def read_lock(lock_path: Path) -> dict[str, object] | None:
    if not lock_path.exists():
        return None
    try:
        value = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateError(f"{lock_path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        raise UpdateError(f"{lock_path}: expected an object with a files map")
    return value


def source_identity(source: dict[str, str]) -> dict[str, str]:
    if source["mode"] == "directory":
        return {
            "mode": "directory",
            "repository": source["repository"],
            "ref": source["ref"],
            "path": source["path"],
        }
    return {"mode": "file", "url": source["url"]}


def make_lock(
    source: dict[str, str], resolved_revision: str, files: dict[str, dict[str, object]]
) -> dict[str, object]:
    return {**source_identity(source), "resolved_revision": resolved_revision, "files": files}


def json_text(value: dict[str, object]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def describe_text_change(current: Path, upstream: Path) -> None:
    if current.suffix.lower() not in {".md", ".py", ".json", ".yaml", ".yml", ".txt"}:
        return
    try:
        old_lines = current.read_text(encoding="utf-8").splitlines(keepends=True) if current.exists() else []
        new_lines = upstream.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return
    diff = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=str(current),
            tofile=f"upstream/{upstream.name}",
            n=2,
        )
    )
    if diff:
        print(diff, end="" if diff.endswith("\n") else "\n")


def remove_empty_parents(path: Path, stop: Path) -> None:
    parent = path.parent
    while parent != stop and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def apply_update(
    skill_dir: Path,
    upstream: Path,
    new_files: dict[str, dict[str, object]],
    old_files: dict[str, dict[str, object]],
    new_lock: dict[str, object],
    expected_name: str | None,
) -> None:
    with tempfile.TemporaryDirectory(prefix=f".{skill_dir.name}.candidate-", dir=skill_dir.parent) as temp:
        candidate = Path(temp) / skill_dir.name
        shutil.copytree(skill_dir, candidate, symlinks=True)

        for relative in sorted(set(old_files) - set(new_files)):
            target = candidate / relative
            if target.exists():
                target.unlink()
                remove_empty_parents(target, candidate)

        for relative in sorted(new_files):
            source_file = upstream / relative
            target = candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)

        (candidate / LOCK_FILE).write_text(json_text(new_lock), encoding="utf-8")
        validate_skill(candidate, expected_name)

        backup = Path(temp) / f"{skill_dir.name}.previous"
        skill_dir.rename(backup)
        try:
            candidate.rename(skill_dir)
        except Exception:
            backup.rename(skill_dir)
            raise
        shutil.rmtree(backup)


def update_skill(
    skill_dir: Path,
    check: bool,
    dry_run: bool,
    force: bool,
    allow_name_mismatch: bool = False,
) -> bool:
    source_path = skill_dir / SOURCE_FILE
    source = parse_source(source_path)
    old_lock = read_lock(skill_dir / LOCK_FILE)
    old_files = old_lock.get("files", {}) if old_lock else {}
    if not isinstance(old_files, dict):
        raise UpdateError(f"{skill_dir / LOCK_FILE}: files must be an object")

    with tempfile.TemporaryDirectory(prefix=f"update-{skill_dir.name}-") as temp:
        temporary_root = Path(temp)
        if source["mode"] == "directory":
            upstream, revision = fetch_directory(source, temporary_root)
        else:
            upstream, revision = fetch_file(source, temporary_root)

        validate_skill(upstream, None if allow_name_mismatch else skill_dir.name)
        new_files = snapshot(upstream)

        conflicts: list[str] = []
        for relative, old_value in old_files.items():
            current_value = fingerprint(skill_dir / relative)
            new_value = new_files.get(relative)
            if current_value != old_value and current_value != new_value:
                conflicts.append(relative)
        if old_lock:
            for relative in set(new_files) - set(old_files):
                current_value = fingerprint(skill_dir / relative)
                if current_value is not None and current_value != new_files[relative]:
                    conflicts.append(relative)
        if conflicts and not force:
            joined = ", ".join(sorted(conflicts))
            raise UpdateError(
                f"{skill_dir.name}: locally modified managed files: {joined}; "
                "review them or rerun with --force"
            )

        changes: list[tuple[str, str]] = []
        for relative in sorted(set(old_files) - set(new_files)):
            if (skill_dir / relative).exists():
                changes.append(("remove", relative))
        for relative in sorted(new_files):
            current = fingerprint(skill_dir / relative)
            if current is None:
                changes.append(("add", relative))
            elif current != new_files[relative]:
                changes.append(("update", relative))

        identity_changed = old_lock is None or any(
            old_lock.get(key) != value for key, value in source_identity(source).items()
        )
        content_changed = bool(changes)
        if old_lock and not identity_changed and old_lock.get("files") == new_files and not content_changed:
            new_lock = old_lock
        else:
            new_lock = make_lock(source, revision, new_files)
        lock_changed = old_lock is None or json_text(old_lock) != json_text(new_lock)

        if not changes and not lock_changed:
            print(f"{skill_dir.name}: up to date")
            return False

        print(f"{skill_dir.name}: update available")
        for action, relative in changes:
            print(f"  {action:6} {relative}")
            if action in {"add", "update"}:
                describe_text_change(skill_dir / relative, upstream / relative)
        if lock_changed:
            print(f"  update {LOCK_FILE}")

        if not check and not dry_run:
            apply_update(
                skill_dir,
                upstream,
                new_files,
                old_files,
                new_lock,
                None if allow_name_mismatch else skill_dir.name,
            )
            print(f"{skill_dir.name}: updated")
        return True


def discover_skills(skills_dir: Path) -> list[Path]:
    return sorted(path.parent for path in skills_dir.rglob(SOURCE_FILE))


def select_skills(skills_dir: Path, names: list[str], update_all: bool) -> list[Path]:
    discovered = discover_skills(skills_dir)
    if update_all:
        return discovered
    if not names:
        raise UpdateError("pass one or more skill names, or use --all")

    by_name: dict[str, list[Path]] = {}
    for path in discovered:
        by_name.setdefault(path.name, []).append(path)

    selected: list[Path] = []
    for name in names:
        matches = by_name.get(name, [])
        if not matches:
            raise UpdateError(f"unknown skill {name!r}")
        if len(matches) > 1:
            raise UpdateError(f"skill name {name!r} is ambiguous")
        selected.append(matches[0])
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skills", nargs="*", help="skill directory names to update")
    parser.add_argument("--all", action="store_true", help="update every discovered skill")
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 if updates exist")
    parser.add_argument("--dry-run", action="store_true", help="show updates without writing")
    parser.add_argument("--force", action="store_true", help="overwrite locally modified managed files")
    parser.add_argument("--allow-name-mismatch", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "skills",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.all and arguments.skills:
        print("error: do not combine skill names with --all", file=sys.stderr)
        return 2
    if arguments.check and arguments.dry_run:
        print("error: do not combine --check with --dry-run", file=sys.stderr)
        return 2

    try:
        selected = select_skills(arguments.skills_dir.resolve(), arguments.skills, arguments.all)
        if not selected:
            raise UpdateError(f"no {SOURCE_FILE} files found below {arguments.skills_dir}")
        changed = False
        for skill in selected:
            changed = update_skill(
                skill,
                arguments.check,
                arguments.dry_run,
                arguments.force,
                arguments.allow_name_mismatch,
            ) or changed
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1 if arguments.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
