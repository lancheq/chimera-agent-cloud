"""Clean control characters (e.g. \x07 BEL) from JSONL dataset files.

Removes C0 control characters except \t \n \r from text and section_title fields.
Writes cleaned output back to the same file.
"""
import json
import re
import sys
from pathlib import Path

# Match control chars except \t(0x09), \n(0x0a), \r(0x0d)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_value(val):
    """Strip control characters from a string."""
    if isinstance(val, str):
        return CTRL_RE.sub("", val)
    return val


def clean_jsonl(path: Path, fields_to_clean: list[str]) -> int:
    """Clean control chars from specified fields in a JSONL file.

    Returns the number of modified records.
    """
    records = []
    modified = 0

    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            changed = False
            for field in fields_to_clean:
                if field in r and r[field] is not None:
                    cleaned = clean_value(r[field])
                    if cleaned != r[field]:
                        r[field] = cleaned
                        changed = True
            if changed:
                modified += 1
            records.append(r)

    # Write back
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return modified


def main():
    base = Path(__file__).resolve().parent.parent / "data_backup" / "guideline_dataset"

    files = [
        (base / "normalized" / "guidelines_unified.jsonl", ["text", "section_title"]),
        (base / "normalized" / "eau_normalized.jsonl", ["text", "section_title"]),
        (base / "qa" / "guideline_qa.jsonl", ["question", "answer"]),
    ]

    total_modified = 0
    for path, fields in files:
        if not path.exists():
            print(f"SKIP {path} (not found)")
            continue

        # Count before
        before = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                for field in fields:
                    if field in r and r[field] and CTRL_RE.search(str(r[field])):
                        before += 1
                        break

        modified = clean_jsonl(path, fields)
        total_modified += modified
        print(f"{path.name}: cleaned {modified} records (had ctrl chars in {before} records)")

    print(f"\nTotal: {total_modified} records cleaned")
    sys.exit(0)


if __name__ == "__main__":
    main()
