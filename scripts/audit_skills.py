"""Audit all skills under ./skills/ and classify them.

Classifies each skill as:
- complete:  parses, non-trivial body, all required bins on PATH
- skeleton:  parses but body is too short / mostly placeholder, or no description
- inactive:  parses fine but required external bins are not installed locally
             (works on a machine where the bin exists; not actually broken)
- broken:    parse error or missing frontmatter — needs author fix

Outputs Markdown to stdout.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Make the project importable when run from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alpha.skills.loader import load_skill_file  # noqa: E402

SKILLS_DIR = ROOT / "skills"
MIN_BODY_CHARS = 300  # arbitrary cutoff for "this is more than a placeholder"


def classify(skill_path: Path) -> tuple[str, str, dict]:
    """Return (status, reason, metadata) for a SKILL.md path."""
    try:
        skill = load_skill_file(skill_path)
    except Exception as e:
        # Scrub the absolute project root from the error so the audit
        # report stays portable across machines and doesn't leak the local
        # username when committed to a public repo.
        reason = str(e).replace(str(ROOT) + "/", "").replace(str(ROOT), ".")
        return "broken", f"parse error: {reason}", {}

    info = {
        "name": skill.name,
        "description": (skill.description or "")[:120],
        "body_chars": len(skill.body),
        "requires_bins": skill.requires_bins,
    }

    if not skill.description:
        return "skeleton", "no description in frontmatter", info

    if len(skill.body) < MIN_BODY_CHARS:
        return "skeleton", f"body too short ({len(skill.body)} chars)", info

    missing_bins = [b for b in skill.requires_bins if shutil.which(b) is None]
    if missing_bins:
        return "inactive", f"missing bins on PATH: {', '.join(missing_bins)}", info

    return "complete", "ok", info


def main() -> int:
    rows: list[tuple[str, str, str, dict]] = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        status, reason, info = classify(skill_md)
        rows.append((status, skill_md.parent.name, reason, info))

    # Order: broken first (action items), then skeleton, then inactive, then complete.
    order = {"broken": 0, "skeleton": 1, "inactive": 2, "complete": 3}
    rows.sort(key=lambda r: (order[r[0]], r[1]))

    counts = {"broken": 0, "skeleton": 0, "inactive": 0, "complete": 0}
    for status, *_ in rows:
        counts[status] += 1

    print("# Skills audit")
    print()
    print(f"Total: **{len(rows)}** skills "
          f"(complete: {counts['complete']}, "
          f"inactive: {counts['inactive']}, "
          f"skeleton: {counts['skeleton']}, "
          f"broken: {counts['broken']}).")
    print()
    print("- **complete**: ready to use as-is.")
    print("- **inactive**: parses fine but the external CLI it wraps "
          "(`requires_bins`) isn't installed on this machine. Install the "
          "binary and the skill becomes complete.")
    print("- **skeleton**: parses but body is a stub (or no description) — "
          "needs author content.")
    print("- **broken**: parse error or missing frontmatter — author fix required.")
    print()
    print("| Status | Skill | Body chars | Requires bins | Notes |")
    print("|---|---|---:|---|---|")
    for status, name, reason, info in rows:
        bins = ", ".join(info.get("requires_bins") or []) or "—"
        chars = info.get("body_chars", "—")
        notes = reason if status != "complete" else info.get("description", "")
        print(f"| {status} | `{name}` | {chars} | {bins} | {notes} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
