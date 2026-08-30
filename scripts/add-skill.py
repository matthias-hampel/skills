#!/usr/bin/env python3
"""Add a complete skill from a GitHub tree URL."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit, urlunsplit


NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9-]+$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class AddError(RuntimeError):
    pass


def parse_github_tree_url(url: str, explicit_ref: str | None = None) -> dict[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port is not None:
        raise AddError("expected an HTTPS github.com URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AddError("GitHub URL must not contain credentials, a query, or a fragment")

    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(parts) < 5 or parts[2] not in {"tree", "blob"}:
        raise AddError("expected a GitHub /tree/ or /blob/ URL pointing to a skill directory")
    if any(not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        raise AddError("GitHub URL contains an unsafe path segment")

    owner = parts[0]
    repository_name = parts[1].removesuffix(".git")
    if not OWNER_RE.fullmatch(owner) or not REPOSITORY_RE.fullmatch(repository_name):
        raise AddError("GitHub owner or repository name is invalid")

    tail = parts[3:]
    if explicit_ref:
        ref_parts = explicit_ref.split("/")
        if any(not part or part in {".", ".."} for part in ref_parts):
            raise AddError("--ref is invalid")
        if tail[: len(ref_parts)] != ref_parts:
            raise AddError("--ref does not match the branch or tag in the GitHub URL")
        ref = explicit_ref
        path_parts = tail[len(ref_parts) :]
    else:
        ref = tail[0]
        path_parts = tail[1:]

    if not path_parts:
        raise AddError("GitHub URL must point to a skill directory")
    upstream_path = PurePosixPath(*path_parts).as_posix()
    normalized_parts = parsed.path.strip("/").split("/")
    normalized_parts[2] = "tree"
    return {
        "repository": f"https://github.com/{owner}/{repository_name}.git",
        "ref": ref,
        "path": upstream_path,
        "name": path_parts[-1],
        "url": urlunsplit(("https", "github.com", "/" + "/".join(normalized_parts), "", "")),
    }


def source_text(repository: str, ref: str, upstream_path: str, source_url: str) -> str:
    return "\n".join(
        [
            "---",
            f"repository: {repository}",
            f"ref: {ref}",
            f"path: {upstream_path}",
            "---",
            "",
            source_url.rstrip("/"),
            "",
        ]
    )


def read_skill_name(skill_file: Path) -> str:
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise AddError(f"{skill_file}: SKILL.md must start with YAML frontmatter")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if key.strip() == "name" and separator:
            name = value.strip().strip("'\"")
            if not NAME_RE.fullmatch(name):
                raise AddError(f"{skill_file}: invalid or missing skill name")
            return name
    raise AddError(f"{skill_file}: invalid or missing skill name")


def add_skill(
    repository: str,
    ref: str,
    upstream_path: str,
    source_url: str,
    skills_dir: Path,
    updater: Path,
) -> Path:
    skills_dir = skills_dir.resolve()
    skills_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="add-skill-", dir=skills_dir.parent) as temporary:
        staging_skills = Path(temporary) / "skills"
        staged_skill = staging_skills / "pending-skill"
        staged_skill.mkdir(parents=True)
        (staged_skill / "SOURCE.md").write_text(
            source_text(repository, ref, upstream_path, source_url),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(updater),
                "--skills-dir",
                str(staging_skills),
                "pending-skill",
                "--allow-name-mismatch",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "skill download failed"
            if detail.startswith("error: "):
                detail = detail.removeprefix("error: ")
            raise AddError(detail)

        name = read_skill_name(staged_skill / "SKILL.md")
        destination = skills_dir / name
        if destination.exists():
            raise AddError(f"{destination}: skill already exists")
        skills_dir.mkdir(parents=True, exist_ok=True)
        staged_skill.rename(destination)

    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="GitHub URL pointing to an upstream skill directory")
    parser.add_argument(
        "--ref",
        help="full branch or tag name when it contains slashes",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "skills",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        source = parse_github_tree_url(arguments.url, arguments.ref)
        destination = add_skill(
            repository=source["repository"],
            ref=source["ref"],
            upstream_path=source["path"],
            source_url=source["url"],
            skills_dir=arguments.skills_dir,
            updater=Path(__file__).with_name("update-skills.py"),
        )
    except AddError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Added {destination.name} to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
