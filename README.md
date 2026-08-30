# AI skill library

Each skill lives below `skills/`. `SOURCE.md` records its upstream location and
`SOURCE.lock.json` records the files managed by the updater.

## Link the library

Link every repository skill into `~/.agents/skills`:

```fish
python3 scripts/link-skills.py --dry-run
python3 scripts/link-skills.py
```

The script leaves correct links alone. It moves existing entries with matching
names into a timestamped backup beside `~/.agents/skills`, then creates absolute
symlinks. Skills that exist only in the target directory remain untouched.

## Add a skill

Pass a GitHub directory URL to the add script:

```fish
python3 scripts/add-skill.py https://github.com/mattpocock/skills/tree/main/skills/engineering/code-review
```

The command downloads the complete directory, validates `SKILL.md`, creates
`SOURCE.md` and `SOURCE.lock.json`, then adds it below `skills/`. It stops without
changing the library when the download or validation fails. It also refuses to
replace a skill that already exists. GitHub `/tree/` and `/blob/` directory URLs
are both accepted; saved source links use `/tree/`.

GitHub URLs do not separate a branch name containing `/` from the directory
path. Pass the complete ref in that case:

```fish
python3 scripts/add-skill.py \
  --ref feature/review-rules \
  https://github.com/owner/repository/tree/feature/review-rules/skills/code-review
```

## Update skills locally

These commands work in Fish:

```fish
python3 scripts/update-skills.py --all
python3 scripts/update-skills.py unslop
python3 scripts/update-skills.py --all --dry-run
python3 scripts/update-skills.py --all --check
```

`--check` exits with status 1 when updates are available. The updater refuses to
overwrite a managed file changed since the previous update. Inspect the change
or pass `--force` when replacing that local edit is intentional.

## Track a complete upstream skill directory

Add `SOURCE.md` to the local skill directory:

```markdown
---
repository: https://github.com/owner/repository.git
ref: main
path: path/to/skill
---

https://github.com/owner/repository/tree/main/path/to/skill
```

The link below the frontmatter is for people browsing the repository. The
updater reads `repository`, `ref`, and `path`, then imports all regular files
from that directory. It never imports symbolic links and never replaces the
local `SOURCE.md` or `SOURCE.lock.json`.

The lock file lists managed files and their checksums. On later updates, files
removed upstream are removed locally. Files added locally remain untouched.

A legacy `SOURCE.md` containing only one raw HTTP URL remains supported. That
mode updates only the adjacent `SKILL.md`.

## Automated pull requests

The `Update skills` workflow runs every Monday at 05:17 UTC and can also be run
manually from the Actions page. It updates the `automation/update-skills`
branch and opens one pull request against the default branch.

The workflow uses the repository `GITHUB_TOKEN`. In the repository settings,
allow GitHub Actions read and write access and allow it to create pull requests.
No extra secret is required.
