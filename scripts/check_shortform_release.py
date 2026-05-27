#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = [
    ROOT / "content_ai_runtime",
    ROOT / "shortform_ai",
    ROOT / "tests",
    ROOT / "Formula" / "shortform-ai.rb",
    ROOT / "docs" / "release-shortform-ai.md",
    ROOT / ".github" / "workflows" / "release-shortform-ai.yml",
    ROOT / "README.md",
    ROOT / "pyproject.toml",
    ROOT / "scripts" / "check_shortform_release.py",
    ROOT / "scripts" / "release_shortform_ai.sh",
]

TEXT_SUFFIXES = {
    ".js",
    ".json",
    ".md",
    ".py",
    ".rb",
    ".sh",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}

SECRET_PATTERNS = [
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{24,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{32,}\b")),
    ("Instagram sessionid", re.compile(r"\b\d{5,}:[A-Za-z0-9_%.\-]{12,}:[A-Za-z0-9_%.\-]{8,}(?::[A-Za-z0-9_%.\-]{8,})?\b")),
    ("Bearer token literal", re.compile(r"\bBearer\s+[A-Za-z0-9_\-.]{32,}\b")),
]

ALLOWLIST_PATTERNS = [
    re.compile(r"sk-test\b"),
    re.compile(r"sk-from-file\b"),
    re.compile(r"REPLACE_WITH_[A-Z0-9_]+"),
    re.compile(r"Bearer \{api_key\}"),
    re.compile(r"12345:plain-session\b"),
    re.compile(r"1234:secret\b"),
    re.compile(r"12345:manual\b"),
]


def _is_allowlisted(value: str) -> bool:
    return any(pattern.search(value) for pattern in ALLOWLIST_PATTERNS)


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for path in SOURCE_PATHS:
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix in TEXT_SUFFIXES and "__pycache__" not in child.parts:
                files.append(child)
    return sorted(set(files))


def _scan_text(text: str, label: str) -> list[str]:
    findings: list[str] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if _is_allowlisted(value):
                continue
            findings.append(f"{label}: possible {name}: {value[:8]}...[redacted]")
    return findings


def scan_sources() -> list[str]:
    findings: list[str] = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT)
        findings.extend(_scan_text(text, str(rel)))
    return findings


def scan_dist(dist_dir: Path) -> list[str]:
    findings: list[str] = []
    if not dist_dir.exists():
        return findings
    for path in sorted(dist_dir.iterdir()):
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as wheel:
                for name in wheel.namelist():
                    if Path(name).suffix not in TEXT_SUFFIXES:
                        continue
                    text = wheel.read(name).decode("utf-8", errors="ignore")
                    findings.extend(_scan_text(text, f"{path.name}:{name}"))
        elif path.suffixes[-2:] == [".tar", ".gz"] or path.suffix in {".gz", ".tgz"}:
            import tarfile

            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if not member.isfile() or Path(member.name).suffix not in TEXT_SUFFIXES:
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    text = extracted.read().decode("utf-8", errors="ignore")
                    findings.extend(_scan_text(text, f"{path.name}:{member.name}"))
    return findings


def check_cli_redaction() -> list[str]:
    secret = ":".join(["30202554619", "verysecretvalue", "moresecretvalue", "signaturevalue"])
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "SHORTFORM_AI_INSTAGRAM_SESSIONID": secret,
        "SHORTFORM_AI_DISABLE_DOTENV": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "shortform_ai.cli", "doctor"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = proc.stdout + proc.stderr
    findings: list[str] = []
    if proc.returncode != 0:
        findings.append("shortform-ai doctor redaction check failed to run")
    if secret in output:
        findings.append("shortform-ai doctor printed a raw Instagram session")
    if "[REDACTED]" not in output:
        findings.append("shortform-ai doctor did not show a redacted session marker")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check shortform-ai release artifacts for accidental secrets.")
    parser.add_argument("--dist", type=Path, help="Optional dist directory to scan after building.")
    args = parser.parse_args(argv)

    findings = scan_sources()
    if args.dist:
        findings.extend(scan_dist(args.dist.resolve()))
    findings.extend(check_cli_redaction())

    if findings:
        print("Sensitive information release check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print("Sensitive information release check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
