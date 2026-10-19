"""Add to .env every setting that .env.example has and .env lacks.

Existing lines are never changed, and values are never printed, because
.env holds secrets. A setting whose example value is empty is not added: an
empty value would override the code's own default. Settings that need a real
value and are still missing or empty are listed by name at the end. Before
writing, the old file is copied to .env.bak (git ignores it).

Usage:
    python scripts/sync_env.py             # adds the missing settings
    python scripts/sync_env.py --dry-run   # only lists them
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Without these the API cannot log users in, read the database or search Booking.
REQUIRED = ("DATABASE_URL", "NEON_AUTH_URL", "APIFY_TOKEN")
# Optional, but a feature is off without them.
RECOMMENDED = {"ANTHROPIC_API_KEY": "χωρίς αυτό ο σύμβουλος τιμών δίνει μόνο στατιστική τιμή"}

# Login moved to Neon Auth: the old Supabase settings are not added back.
SKIPPED_PREFIXES = ("SUPABASE_",)

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    """KEY -> raw value of every assignment line; comments and blanks are skipped."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if match:
            values.setdefault(match.group(1), match.group(2).strip().strip("'\""))
    return values


def missing_settings(example_text: str, env_text: str) -> list[tuple[str, str]]:
    """(key, example value) for each non-empty example setting absent from .env, in file order."""
    present = parse_env(env_text)
    return [
        (key, value)
        for key, value in parse_env(example_text).items()
        if key not in present and value != "" and not key.startswith(SKIPPED_PREFIXES)
    ]


def unset_required(env_text: str) -> list[str]:
    """Required/recommended keys that .env lacks or leaves empty."""
    present = parse_env(env_text)
    return [key for key in (*REQUIRED, *RECOMMENDED) if not present.get(key)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list the missing settings without writing")
    args = parser.parse_args()

    example_path = PROJECT_ROOT / ".env.example"
    env_path = PROJECT_ROOT / ".env"
    # utf-8-sig: a .env saved by a Windows editor starts with a BOM.
    example_text = example_path.read_text(encoding="utf-8-sig")
    env_text = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""

    to_add = missing_settings(example_text, env_text)
    if to_add:
        print(f"{'Θα προστεθούν' if args.dry_run else 'Προστέθηκαν'} {len(to_add)} ρυθμίσεις:")
        for key, _ in to_add:
            print(f"  + {key}")
    else:
        print("Το .env έχει ήδη όλες τις ρυθμίσεις του .env.example.")

    if to_add and not args.dry_run:
        if env_path.exists():
            shutil.copyfile(env_path, env_path.with_name(".env.bak"))
        block = "\n".join(f"{key}={value}" for key, value in to_add)
        separator = "" if not env_text or env_text.endswith("\n") else "\n"
        with env_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{separator}\n# Added from .env.example by scripts/sync_env.py\n{block}\n")

    final_text = env_text if args.dry_run else env_path.read_text(encoding="utf-8-sig")
    still_unset = unset_required(final_text)
    if still_unset:
        print("\nΒάλε τιμή στο .env για:")
        for key in still_unset:
            hint = RECOMMENDED.get(key, "υποχρεωτικό")
            print(f"  - {key} ({hint})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
