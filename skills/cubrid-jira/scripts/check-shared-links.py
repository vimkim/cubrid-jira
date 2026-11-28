#!/usr/bin/env python3
"""Reject known personal viewing URLs in material intended for other readers."""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


PRIVATE_HOSTS = {"dev2.baboon-rudd.ts.net"}
URL_PATTERN = re.compile(
    r"""(?:[a-z][a-z0-9+.-]*:)?//[^\s<>\"'`()\[\]{}]+""", re.IGNORECASE
)


def check_file(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"{path}: error: cannot read UTF-8 material: {error}", file=sys.stderr)
        return False

    valid = True
    for line_number, line in enumerate(content.splitlines(), start=1):
        for match in URL_PATTERN.finditer(line):
            url = match.group().rstrip(".,;!?")
            try:
                hostname = urlsplit(url).hostname
            except ValueError:
                continue
            if hostname and hostname.rstrip(".").lower() in PRIVATE_HOSTS:
                print(
                    f"{path}:{line_number}: error: personal viewing URL uses {hostname}; "
                    "use a verified URL accessible to the intended readers or "
                    "include the evidence inline and omit the link",
                    file=sys.stderr,
                )
                valid = False
    return valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", metavar="FILE", type=Path, nargs="+")
    arguments = parser.parse_args()
    results = [check_file(path) for path in arguments.files]
    if not all(results):
        return 1
    print("Shared link check passed (known personal hosts only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
