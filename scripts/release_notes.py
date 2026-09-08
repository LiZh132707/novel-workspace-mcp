"""Extract exactly one released version from CHANGELOG.md for gh release."""
import argparse
from pathlib import Path
import re


def extract_release_notes(changelog: str, version: str) -> str:
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a released semantic version, such as 2.8.0")
    headings = list(re.finditer(r"^## \[([^\]]+)\].*$", changelog, re.MULTILINE))
    matching = [i for i, heading in enumerate(headings) if heading.group(1) == version]
    if len(matching) != 1:
        raise ValueError(f"Expected exactly one changelog section for {version}")
    index = matching[0]
    start = headings[index].end()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
    body = changelog[start:end]
    body = re.split(r"^\[[^\]]+\]:\s+https?://", body, maxsplit=1, flags=re.MULTILINE)[0].strip()
    if not body:
        raise ValueError(f"Release {version} has no notes")
    return f"## {version}\n\n{body}\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.changelog.resolve():
        parser.error("Output must differ from the changelog")
    notes = extract_release_notes(args.changelog.read_text(encoding="utf-8"), args.version)
    args.output.write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
