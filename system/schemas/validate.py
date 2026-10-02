#!/usr/bin/env python3
"""
RB Schema Validator

Usage:
    python3 validate.py                       # Validate the default targets
                                              # (baseline_index.json + strategic_operators.yaml + ecosystem_intelligence.json)
    python3 validate.py path/to/file.json     # Validate a specific JSON file
    python3 validate.py path/to/file.yaml     # Validate a specific YAML file
    python3 validate.py --schema PATH FILE    # Use a non-default schema
    python3 validate.py --baseline-only       # Only the baseline check (backward compat)
    python3 validate.py --strategic-operators-only
    python3 validate.py --ecosystem-only

Exit codes:
    0 — valid
    1 — validation failure (details on stderr)
    2 — runtime error (file not found, parse error, missing deps, etc.)

Designed to be a pre-commit gate before any model writes back to canonical
files. Any procedure (P-001, P-002, P-031, etc.) that mutates a canonical
file should call this validator first and refuse to write if it fails.

YAML targets are loaded with PyYAML when available; YAML targets fail with
status 2 when PyYAML is not installed.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    try:
        from jsonschema import Draft202012Validator as Validator
    except ImportError:
        from jsonschema import Draft7Validator as Validator
except ImportError:
    print(
        "jsonschema not installed. Install with: pip install jsonschema --break-system-packages",
        file=sys.stderr,
    )
    sys.exit(2)


SYSTEM_DIR = Path(__file__).resolve().parent.parent

DEFAULT_BASELINE_TARGET = SYSTEM_DIR / "baseline_index.json"
DEFAULT_BASELINE_SCHEMA = SYSTEM_DIR / "schemas" / "baseline.schema.json"

DEFAULT_OPERATORS_TARGET = SYSTEM_DIR / "strategic_operators.yaml"
DEFAULT_OPERATORS_SCHEMA = SYSTEM_DIR / "schemas" / "strategic_operators.schema.json"

DEFAULT_ECOSYSTEM_TARGET = SYSTEM_DIR / "ecosystem_intelligence.json"
DEFAULT_ECOSYSTEM_SCHEMA = SYSTEM_DIR / "schemas" / "ecosystem_intelligence.schema.json"


def _stringify_dates(obj):
    """Recursively convert datetime.date / datetime.datetime to ISO strings.

    PyYAML auto-coerces bare ISO-format date scalars into datetime.date
    objects, which then fail JSON-schema string validation. Normalize back
    to strings before validation so we don't have to force quoted dates in
    canonical YAML.
    """
    from datetime import date, datetime
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _stringify_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_dates(v) for v in obj]
    return obj


def _load_data(target_path: Path):
    """Load JSON or YAML based on extension. YAML requires PyYAML."""
    suffix = target_path.suffix.lower()
    raw = target_path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise RuntimeError(
                "PyYAML required to validate YAML targets. Install with: "
                "pip install pyyaml --break-system-packages"
            )
        return _stringify_dates(yaml.safe_load(raw))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON parse failure: {exc}")


def validate(target_path: Path, schema_path: Path) -> int:
    if not target_path.exists():
        print(f"ERROR: target file not found: {target_path}", file=sys.stderr)
        return 2
    if not schema_path.exists():
        print(f"ERROR: schema file not found: {schema_path}", file=sys.stderr)
        return 2

    try:
        with open(schema_path) as f:
            schema = json.load(f)
        data = _load_data(target_path)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON parse failure: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    validator = Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))

    if not errors:
        if isinstance(data, list):
            item_count = len(data)
        elif isinstance(data, dict) and "operators" in data and isinstance(data["operators"], list):
            item_count = len(data["operators"])
        else:
            item_count = 1
        print(f"OK — {target_path.name} validates against {schema_path.name} ({item_count} items)")
        return 0

    # Print all errors with path context
    print(f"FAILED — {len(errors)} validation error(s) in {target_path.name}:\n", file=sys.stderr)
    for i, err in enumerate(errors, 1):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        print(f"  [{i}] at {path}", file=sys.stderr)
        print(f"      {err.message}", file=sys.stderr)
        # For array items, include the entry id if available
        if err.absolute_path and isinstance(err.absolute_path[0], (int, str)):
            try:
                entry = data
                for step in list(err.absolute_path)[: -1 if not isinstance(err.absolute_path[-1], int) else None]:
                    entry = entry[step]
                if isinstance(entry, dict) and "id" in entry:
                    print(f"      (entry id: {entry['id']})", file=sys.stderr)
            except (IndexError, KeyError, TypeError):
                pass
        print("", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RB canonical files against schema.")
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help=(
            "Optional path to a single target file. When omitted, both the "
            "baseline, strategic_operators, and ecosystem_intelligence files are validated."
        ),
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="Path to a JSON Schema file (overrides default schema selection by target).",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Validate only the baseline target.",
    )
    parser.add_argument(
        "--strategic-operators-only",
        action="store_true",
        help="Validate only the strategic_operators target.",
    )
    parser.add_argument(
        "--ecosystem-only",
        action="store_true",
        help="Validate only the ecosystem_intelligence target.",
    )
    args = parser.parse_args()

    # Single-target invocation
    if args.target:
        target = Path(args.target)
        if args.schema:
            schema = Path(args.schema)
        elif target.name == DEFAULT_OPERATORS_TARGET.name:
            schema = DEFAULT_OPERATORS_SCHEMA
        elif target.name == DEFAULT_ECOSYSTEM_TARGET.name:
            schema = DEFAULT_ECOSYSTEM_SCHEMA
        else:
            schema = DEFAULT_BASELINE_SCHEMA
        return validate(target, schema)

    # Default: both canonical files
    targets: list[tuple[Path, Path]] = []
    if args.strategic_operators_only:
        targets = [(DEFAULT_OPERATORS_TARGET, DEFAULT_OPERATORS_SCHEMA)]
    elif args.ecosystem_only:
        targets = [(DEFAULT_ECOSYSTEM_TARGET, DEFAULT_ECOSYSTEM_SCHEMA)]
    elif args.baseline_only:
        targets = [(DEFAULT_BASELINE_TARGET, DEFAULT_BASELINE_SCHEMA)]
    else:
        targets = [
            (DEFAULT_BASELINE_TARGET, DEFAULT_BASELINE_SCHEMA),
            (DEFAULT_OPERATORS_TARGET, DEFAULT_OPERATORS_SCHEMA),
            (DEFAULT_ECOSYSTEM_TARGET, DEFAULT_ECOSYSTEM_SCHEMA),
        ]

    worst = 0
    for t, s in targets:
        if not t.exists():
            # Missing newer canonical files is not a failure when running the
            # multi-target default — older installs may not have them yet.
            if t in {DEFAULT_OPERATORS_TARGET, DEFAULT_ECOSYSTEM_TARGET} and not (
                args.strategic_operators_only or args.ecosystem_only
            ):
                print(
                    f"SKIP — {t.name} not present yet (pre-RB-9.1 install); "
                    "create the canonical file to enable validation.",
                )
                continue
            print(f"ERROR: target file not found: {t}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        rc = validate(t, s)
        worst = max(worst, rc)
    return worst


if __name__ == "__main__":
    sys.exit(main())
