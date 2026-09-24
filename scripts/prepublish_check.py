"""Pre-publish scan: stop anything private from reaching the public repository.

Looks at every file that would be committed (``git ls-files`` inside a git checkout; otherwise the working tree
filtered by every ``.gitignore``, with the same rules git uses) and fails if any of them holds:

- a secret: API keys and tokens in their known shapes, private keys, a secret-named setting given a value (``token``,
  ``secret``, ``password``, ``..._API_KEY``), or a credential after ``Bearer`` or ``Basic`` in an Authorization header;
- a local environment file (``.env``, ``.env.local``, ``cloudflare.env``; the ``.env.example`` template is fine, and
  so is ``web/.env.production``, the website's public build settings, as long as it holds only ``VITE_`` variables);
- an absolute path on someone's computer (``C:/Users/...``, ``/Users/<name>/``, ``/home/<name>/``, ``/e/...``);
- a private name (other clients and accounts), listed one per line in a local, git-ignored file
  (``.deploy/private_names.txt``, or the file named by ``EVIDENCELINE_PRIVATE_NAMES``) so no name, and no hash of
  one, is published; without that file this rule is skipped and the scan says so;
- job-search wording (salary, interview, hiring manager, a job application, cover letter, recruiter);
- an em or en dash in Markdown, HTML or plain text, or in the website's own source and proxy code;
- a file over 5 MB.

Each hit is printed as ``path:line: [rule] detail``. Exit status 1 when anything is found, 0 when clean.

    python scripts/prepublish_check.py            # scan the repository this script lives in
    python scripts/prepublish_check.py --list     # also print every file that was scanned
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pathspec

ROOT = Path(__file__).resolve().parents[1]
SELF = PurePosixPath("scripts/prepublish_check.py")
MAX_BYTES = 5 * 1024 * 1024
BINARY_PROBE = 8192

ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
PUBLIC_ENV_FILES = frozenset({PurePosixPath("web/.env.production")})
"""Environment files that are published on purpose: the website's build settings. Vite copies every ``VITE_``
variable into the public JavaScript bundle, so these hold only public values; any other variable in them is flagged,
and the secret rules still run on every line."""
PUBLIC_ENV_VARIABLE = re.compile(r"VITE_[A-Z0-9_]+")

EM_DASH = "\N{EM DASH}"
EN_DASH = "\N{EN DASH}"
DASH = re.compile(f"[{EN_DASH}{EM_DASH}]")
DASH_SUFFIXES = frozenset({".md", ".markdown", ".html", ".htm", ".txt"})
UI_DIRS = (PurePosixPath("web/src"), PurePosixPath("web/functions"))
UI_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".json", ".svg"})

PRIVATE_NAMES_FILE = Path(os.environ.get("EVIDENCELINE_PRIVATE_NAMES") or ROOT / ".deploy" / "private_names.txt")
"""Local list of private names, one per line (never committed). Each is compared lower case with spaces and
punctuation removed, so "Acme Co", "acme-co" and "AcmeCo" all match, against runs of one to three words."""


def _load_private_names(path: Path = PRIVATE_NAMES_FILE) -> frozenset[str]:
    if not path.is_file():
        return frozenset()
    names = (re.sub(r"[^a-z0-9]", "", line.lower()) for line in path.read_text(encoding="utf-8").splitlines())
    return frozenset(name for name in names if name and not name.startswith("#"))


PRIVATE_NAMES = _load_private_names()
NAME_WINDOW = 3
WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    detail: str
    secret: bool = False
    """Hide the matched text in the report, so running the scan does not print the secret."""


_TOKEN_VALUE = r"(?=[A-Za-z0-9_./+~=-]*[0-9])(?=[A-Za-z0-9_./+~=-]*[A-Za-z])[A-Za-z0-9_./+~=-]{20,}"
"""A credential-shaped value: 20 or more token characters with at least one digit and one letter. Many tokens
(Cloudflare's among them) have no fixed prefix, so they are only recognisable next to a name such as ``token`` or
``secret``, or after ``Bearer`` in an Authorization header. Placeholders such as ``<token>``,
``$CLOUDFLARE_API_TOKEN`` or ``YOUR_TOKEN_HERE`` do not match."""

SECRET_RULES = (
    Rule("secret", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic API key", secret=True),
    Rule("secret", re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "API key in the sk- format", secret=True),
    Rule("secret", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token", secret=True),
    Rule("secret", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"), "GitHub fine-grained token", secret=True),
    Rule("secret", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key", secret=True),
    Rule("secret", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key", secret=True),
    Rule("secret", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "Slack token", secret=True),
    Rule("secret", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key", secret=True),
    Rule(
        "secret",
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)\b"
            rf"[\"']?\s*[:=]\s*[\"']?{_TOKEN_VALUE}"
        ),
        "a secret-named setting with a value",
        secret=True,
    ),
    Rule(
        "secret",
        re.compile(rf"(?i)\b(?:Bearer|Basic)\s+{_TOKEN_VALUE}"),
        "a credential in an Authorization header",
        secret=True,
    ),
)

_SEP = r"[\\/]+"
"""One or more slashes or backslashes, so escaped JSON paths (two backslashes) match too."""
_NOT_AFTER = r"(?<![A-Za-z\\])"
"""Not part of a longer word, a URL scheme or a regular expression escape such as a backslash d."""
PATH_RULES = (
    Rule(
        "local-path",
        re.compile(rf"(?i){_NOT_AFTER}[A-Z]:{_SEP}(?:Users|Documents and Settings){_SEP}"),
        "Windows user folder path",
    ),
    Rule("local-path", re.compile(rf"(?i){_NOT_AFTER}[D-Z]:{_SEP}[A-Za-z]"), "path on a local data drive"),
    Rule("local-path", re.compile(r"(?<![A-Za-z0-9.])/(?:Users|home)/[A-Za-z0-9._-]+/"), "home folder path"),
    Rule("local-path", re.compile(r"(?<![A-Za-z0-9.:/])/[a-z]/[A-Za-z0-9 ._-]+/"), "Git Bash drive path"),
)
"""System paths such as C:/Program Files and placeholders such as C:/path/to are not personal, so only user
folders, non-system drives (D: to Z:) and home folders count."""

JOB_RULES = (
    Rule("job-search", re.compile(r"(?i)\bsalar(?:y|ies)\b"), "salary"),
    Rule("job-search", re.compile(r"(?i)\binterview(?:s|ed|ing|er|ers)?\b"), "interview"),
    Rule("job-search", re.compile(r"(?i)\bhiring\b"), "hiring"),
    Rule("job-search", re.compile(r"(?i)\b(?:job|my|your|our)\s+applications?\b"), "a job application"),
    Rule(
        "job-search",
        re.compile(
            r"(?i)\b(?:application|apply|applying|applied)\s+(?:for|to)\s+(?:the\s+|a\s+|this\s+)?"
            r"(?:role|job|position|vacancy)\b"
        ),
        "applying for a role",
    ),
    Rule("job-search", re.compile(r"(?i)\bcover\s+letters?\b"), "cover letter"),
    Rule("job-search", re.compile(r"(?i)\brecruit(?:er|ers|ment|ing)\b"), "recruiting"),
    Rule("job-search", re.compile("(?i)\\b(?:résumé|resumé|résume)s?\\b"), "résumé (CV)"),
)


@dataclass(frozen=True, slots=True)
class Hit:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: [{self.rule}] {self.detail}"


@dataclass(frozen=True, slots=True)
class Allowed:
    """A known, reviewed hit: a rule may fire in files under ``prefix`` on lines containing ``marker``."""

    prefix: str
    rule: str
    marker: str
    reason: str


ALLOWED = (
    Allowed(
        "evals/",
        "job-search",
        '"question":',
        "evaluation sets deliberately ask off-topic questions (for example about pay) that must come back not covered",
    ),
    Allowed(
        "web/public/data/answers.json",
        "job-search",
        '"excerpt":',
        "excerpts are quoted verbatim from the public guidance (site history interviews are an investigation step)",
    ),
    Allowed(
        "web/public/data/answers.json",
        "job-search",
        '"answer":',
        "prepared answers paraphrase the same guidance (a preliminary site investigation includes interviews with "
        "owners and neighbours)",
    ),
)


def _allowed(hit: Hit, line: str) -> bool:
    return any(hit.path.startswith(a.prefix) and hit.rule == a.rule and a.marker in line for a in ALLOWED)


# Listing the files that would be committed


def _git_files(root: Path) -> list[PurePosixPath] | None:
    """Tracked plus untracked-but-not-ignored files, when ``root`` is the top of a git checkout; else None."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return None
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = listed.stdout.decode("utf-8").split("\0")
    return sorted(PurePosixPath(n) for n in names if n and (root / n).is_file())


def _load_ignore(folder: Path) -> pathspec.GitIgnoreSpec | None:
    source = folder / ".gitignore"
    if not source.is_file():
        return None
    return pathspec.GitIgnoreSpec.from_lines(source.read_text(encoding="utf-8").splitlines())


def _ignored(rel: PurePosixPath, is_dir: bool, specs: Sequence[tuple[PurePosixPath, pathspec.GitIgnoreSpec]]) -> bool:
    """Git's rule: the deepest .gitignore with a matching pattern decides, and within one file the last match."""
    for base, spec in reversed(specs):
        inner = rel.relative_to(base).as_posix() if base.parts else rel.as_posix()
        result = spec.check_file(inner + "/" if is_dir else inner)
        if result.include is not None:
            return result.include
    return False


def _walk_files(root: Path) -> list[PurePosixPath]:
    """Every file git would offer to commit in a fresh repository here: ``.git`` and ignored folders are pruned
    (a file inside an ignored folder can never be re-included, as in git)."""
    found: list[PurePosixPath] = []

    def visit(folder: Path, rel: PurePosixPath, specs: list[tuple[PurePosixPath, pathspec.GitIgnoreSpec]]) -> None:
        own = _load_ignore(folder)
        chain = [*specs, (rel, own)] if own is not None else specs
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                child = rel / entry.name
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != ".git" and not _ignored(child, True, chain):
                        visit(Path(entry.path), child, chain)
                elif entry.is_file(follow_symlinks=False) and not _ignored(child, False, chain):
                    found.append(child)

    visit(root, PurePosixPath(), [])
    return found


def files_to_publish(root: Path) -> list[PurePosixPath]:
    listed = _git_files(root)
    return listed if listed is not None else _walk_files(root)


# Checks


def _is_env_file(rel: PurePosixPath) -> bool:
    name = rel.name.lower()
    if name in ENV_TEMPLATES or rel in PUBLIC_ENV_FILES:
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _checks_dashes(rel: PurePosixPath) -> bool:
    if rel.parts[:3] == ("web", "public", "data"):
        return False  # generated data: excerpts are quoted verbatim from the public documents
    if rel.suffix.lower() in DASH_SUFFIXES:
        return True
    return rel.suffix.lower() in UI_SUFFIXES and any(rel.is_relative_to(d) for d in UI_DIRS)


def _private_names(line: str) -> Iterator[str]:
    words = [w.lower() for w in WORD.findall(line)]
    for start in range(len(words)):
        for size in range(1, NAME_WINDOW + 1):
            joined = "".join(words[start : start + size])
            if start + size <= len(words) and joined in PRIVATE_NAMES:
                yield " ".join(words[start : start + size])


def _shown(line: str) -> str:
    """The line, shortened, with dashes spelled out so the report reads the same in any terminal."""
    return line.strip()[:120].replace(EM_DASH, "[em dash]").replace(EN_DASH, "[en dash]")


def _excerpt(line: str, match: re.Match[str], rule: Rule) -> str:
    if rule.secret:
        return f"{rule.detail} ({match.group(0)[:6]}... hidden)"
    return f"{rule.detail}: {_shown(line)}"


def scan_text(rel: PurePosixPath, text: str) -> Iterator[Hit]:
    own_file = rel == SELF
    dashes = _checks_dashes(rel)
    rules = SECRET_RULES if own_file else SECRET_RULES + PATH_RULES + JOB_RULES
    path = rel.as_posix()
    for number, line in enumerate(text.splitlines(), start=1):
        found: list[Hit] = []
        for rule in rules:
            match = rule.pattern.search(line)
            if match:
                found.append(Hit(path, number, rule.name, _excerpt(line, match, rule)))
        if not own_file:
            found.extend(
                Hit(path, number, "private-name", f"private name {n!r}: {_shown(line)}") for n in _private_names(line)
            )
        if dashes and DASH.search(line):
            found.append(Hit(path, number, "dash", f"em or en dash: {_shown(line)}"))
        yield from (hit for hit in found if not _allowed(hit, line))


def _read_text(path: Path) -> str | None:
    """The file as text, or None for a binary file (a zero byte early on, or not UTF-8)."""
    data = path.read_bytes()
    if b"\0" in data[:BINARY_PROBE]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_file(root: Path, rel: PurePosixPath) -> Iterator[Hit]:
    path = root / rel
    size = path.stat().st_size
    if size > MAX_BYTES:
        yield Hit(rel.as_posix(), 0, "large-file", f"{size / (1024 * 1024):.1f} MB, over the 5 MB limit")
    if _is_env_file(rel):
        yield Hit(rel.as_posix(), 0, "env-file", "a local environment file; commit a .env.example template instead")
    for name in _private_names(rel.as_posix()):
        yield Hit(rel.as_posix(), 0, "private-name", f"private name {name!r} in the file name")
    text = _read_text(path)
    if text is not None:
        if rel in PUBLIC_ENV_FILES:
            yield from _non_public_settings(rel, text)
        yield from scan_text(rel, text)


def _non_public_settings(rel: PurePosixPath, text: str) -> Iterator[Hit]:
    """Every setting in a published build-settings file whose name is not a public ``VITE_`` variable."""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = stripped.removeprefix("export ").split("=", 1)[0].strip()
        if "=" not in stripped or not PUBLIC_ENV_VARIABLE.fullmatch(name):
            yield Hit(
                rel.as_posix(),
                number,
                "env-file",
                f"only public VITE_ build settings belong in this published file, not {name[:40]!r}",
            )


def scan(root: Path, files: Iterable[PurePosixPath]) -> list[Hit]:
    return [hit for rel in files for hit in scan_file(root, rel)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if anything private would be published.")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository folder (default: this checkout)")
    parser.add_argument("--list", action="store_true", help="print every file scanned")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    files = files_to_publish(root)
    if args.list:
        for rel in files:
            print(f"scanned: {rel.as_posix()}")
    hits = scan(root, files)
    for hit in hits:
        print(hit)
    rules = sorted({h.rule for h in hits})
    if hits:
        print(
            f"\n{len(hits)} problem(s) in {len({h.path for h in hits})} file(s) ({', '.join(rules)}); "
            f"{len(files)} files scanned. Fix them before publishing."
        )
        return 1
    if not PRIVATE_NAMES:
        print(f"note: no private names file ({PRIVATE_NAMES_FILE.name}); the private-name rule was skipped.")
    print(f"Clean: {len(files)} files scanned, nothing private found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
