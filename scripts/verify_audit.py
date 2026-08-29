#!/usr/bin/env python3
"""
Independently re-walk the stored audit ledger and report whether it is intact.

Run by `make verify-audit`, exposed over HTTP as GET /audit/verify, and
demonstrable on stage. Exits non-zero on a broken chain so it can gate CI.

This deliberately shares only `core.audit` with the writing path — it re-reads
the rows and recomputes every digest from scratch rather than trusting any
cached head.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Runnable directly from a clone with no install step.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audit import AuditEventType
from services.config import SETTINGS
from services.store.repo import Repository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=SETTINGS.database_url)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    repo = Repository(args.database_url)
    chain = repo.load_chain()
    result = repo.verify()

    if not args.quiet:
        print(f"ledger    {args.database_url}")
        print(f"records   {result.length}")
        print(f"head      {result.head}")
        if chain:
            print(f"span      {chain[0].ts.isoformat()} .. {chain[-1].ts.isoformat()}")
            counts = Counter(r.event_type.value for r in chain)
            print("events")
            for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"  {n:>6}  {name}")
        print()

    print(result.summary())
    if not result.ok:
        for failure in result.failures:
            print(f"  seq {failure['seq']}: {failure['reason']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
