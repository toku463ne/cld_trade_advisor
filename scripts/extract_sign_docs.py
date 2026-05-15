"""Extract sign-module docstrings + benchmark comments to docs/signs/<name>.md.

One-shot conversion. After running, each src/signs/<name>.py keeps only a
one-line module docstring referring to docs/signs/<name>.md. The full original
content lives in markdown and never triggers the rebench-staleness hash.

Idempotent: skips files whose header has already been converted (i.e. the
module docstring is a single line containing "See docs/signs/").
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIGNS = REPO / "src" / "signs"
DOCS  = REPO / "docs" / "signs"
DOCS.mkdir(parents=True, exist_ok=True)

SKIP = {"__init__", "base"}
ALREADY_DONE_RE = re.compile(r'See\s+docs/signs/', re.IGNORECASE)


def _find_boundary(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if line.startswith("from __future__") or line.startswith("import "):
            return i
    return None


def _docstring_to_md(doc_body: str) -> str:
    """Body of the module docstring → markdown.

    Heuristic: a line that ends with a colon and is followed by indented
    bullets becomes an `## H2`. Indented `- foo` stays as `- foo`. Otherwise
    pass through with normalised whitespace.
    """
    lines = doc_body.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Indented bullet → dedent
        if stripped.startswith("- "):
            out.append(stripped)
        else:
            out.append(stripped)
    # collapse runs of blank lines
    md: list[str] = []
    blank = False
    for line in out:
        if line == "":
            if not blank:
                md.append("")
            blank = True
        else:
            md.append(line)
            blank = False
    return "\n".join(md).strip() + "\n"


def _comment_block_to_md(block: list[str]) -> str:
    """The `# ── Benchmark ──`-style comment block → markdown.

    We keep the original formatting inside a fenced ``` block so the shell
    commands and ASCII tables remain legible.
    """
    if not block:
        return ""
    cleaned = []
    for line in block:
        # Drop the leading "# " or "#"
        if line.startswith("# "):
            cleaned.append(line[2:])
        elif line == "#":
            cleaned.append("")
        else:
            cleaned.append(line)
    body = "\n".join(cleaned).strip("\n")
    return "## Benchmark notes\n\n```\n" + body + "\n```\n"


def _convert_one(py_path: Path) -> tuple[bool, str]:
    text = py_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    boundary = _find_boundary(lines)
    if boundary is None:
        return False, "no code boundary"

    header = lines[:boundary]
    code   = "\n".join(lines[boundary:])

    # Already converted? — module docstring fits on one line + references docs/signs
    if header and header[0].count('"""') >= 2 and ALREADY_DONE_RE.search(header[0]):
        return False, "already converted"

    # Find the module docstring boundaries
    if not header or not header[0].lstrip().startswith('"""'):
        return False, "no module docstring"

    # Walk forward to closing """
    doc_end = None
    if header[0].rstrip().endswith('"""') and len(header[0].strip()) > 3:
        # single-line docstring on row 0
        doc_end = 0
    else:
        for i in range(1, len(header)):
            if header[i].rstrip().endswith('"""'):
                doc_end = i
                break
    if doc_end is None:
        return False, "unterminated docstring"

    # Extract the docstring body (lines 1..doc_end-1; the first/last lines hold """)
    if doc_end == 0:
        doc_body = header[0].strip().strip('"').strip()
    else:
        first = header[0].lstrip()[3:]  # strip leading """
        last  = header[doc_end].rstrip()[:-3]  # strip trailing """
        middle_lines = header[1:doc_end]
        body_lines = []
        if first.strip():
            body_lines.append(first)
        body_lines.extend(middle_lines)
        if last.strip():
            body_lines.append(last)
        doc_body = "\n".join(body_lines)

    # Take the title: first line of docstring → H1 in markdown
    doc_body = doc_body.strip("\n")
    first_line, _, rest = doc_body.partition("\n")
    title = first_line.strip().rstrip(".")

    # Comment block after the docstring (the # ── Benchmark ── chunk)
    comment_block: list[str] = []
    for i in range(doc_end + 1, len(header)):
        comment_block.append(header[i])
    # Strip leading/trailing blank lines from the block
    while comment_block and not comment_block[0].strip():
        comment_block.pop(0)
    while comment_block and not comment_block[-1].strip():
        comment_block.pop()

    # Build markdown
    md_parts: list[str] = [f"# {title}\n"]
    if rest.strip():
        md_parts.append(_docstring_to_md(rest))
    if comment_block:
        if md_parts and md_parts[-1] and not md_parts[-1].endswith("\n\n"):
            md_parts.append("\n")
        md_parts.append(_comment_block_to_md(comment_block))
    md = "".join(md_parts).rstrip() + "\n"

    md_path = DOCS / (py_path.stem + ".md")
    md_path.write_text(md, encoding="utf-8")

    # Replace header with one-line pointer docstring
    new_first = f'"""{title}. See docs/signs/{py_path.stem}.md."""'
    new_text = new_first + "\n\n" + code + "\n"
    py_path.write_text(new_text, encoding="utf-8")

    return True, f"wrote {md_path.name}"


def main() -> None:
    rows: list[tuple[str, bool, str]] = []
    for py_path in sorted(SIGNS.glob("*.py")):
        if py_path.stem in SKIP:
            continue
        ok, msg = _convert_one(py_path)
        rows.append((py_path.name, ok, msg))
    for name, ok, msg in rows:
        flag = "✓" if ok else "·"
        print(f"  {flag} {name:20s} {msg}")


if __name__ == "__main__":
    main()
