#!/usr/bin/env python3
"""Keep push_relay/copy/ in step with the Voltmoss app's alert copy.

The relay renders every notification from the `alerts` block of the app's
translation bundles, so a push and the alert the app posts itself must say the
same words. The app is the source of that copy; push_relay/copy/ holds the
relay's snapshot of it. Run from the repository root:

    python3 -m push_relay.sync_copy /path/to/j5_ev_app/assets/translations
    python3 -m push_relay.sync_copy /path/to/j5_ev_app/assets/translations --check

The first writes the snapshot. The second changes nothing and exits 1 when the
snapshot no longer matches the app; run it before every relay deploy.
"""

import argparse
import json
import sys
from pathlib import Path

from .relay import LOCALES


COPY_DIR = Path(__file__).resolve().parent / "copy"


def snapshot(source, locale):
    """The relay's copy for one locale: the app bundle's `alerts` block, nothing else."""
    with (Path(source) / f"{locale}.json").open(encoding="utf-8") as f:
        alerts = json.load(f)["alerts"]
    return json.dumps({"alerts": alerts}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="push_relay.sync_copy",
                                     description="Sync the relay's alert copy from the app.")
    parser.add_argument("source", help="the app's assets/translations directory")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if push_relay/copy/ differs from the app")
    args = parser.parse_args(argv)

    stale = []
    for locale in sorted(LOCALES):
        want = snapshot(args.source, locale)
        path = COPY_DIR / f"{locale}.json"
        if path.exists() and path.read_text(encoding="utf-8") == want:
            continue
        stale.append(locale)
        if not args.check:
            COPY_DIR.mkdir(exist_ok=True)
            path.write_text(want, encoding="utf-8")

    if args.check:
        if stale:
            print("push_relay/copy/ is behind the app for: " + ", ".join(stale) +
                  ". Run without --check, review the diff, then redeploy the relay.",
                  file=sys.stderr)
            return 1
        print("push_relay/copy/ matches the app.")
        return 0
    print("updated: " + ", ".join(stale) if stale else "push_relay/copy/ already matches the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
