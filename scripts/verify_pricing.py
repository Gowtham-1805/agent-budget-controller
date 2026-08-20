#!/usr/bin/env python3
"""Print a checklist of real-provider rates that need re-verifying.

`pricing/catalog.json` promises exactly this: every real (non-``test``) entry
carries a ``verified_at`` date, and the file's own ``_README`` says "run `make
verify-pricing` for the checklist." This is that checklist.

It does not call any provider API — pricing pages are not machine-readable in a
stable way across providers — it prints what is currently loaded, flags entries
older than ``--staleness-days`` (default 90), and reminds you to cross-check each
one against the provider's own pricing page before trusting it for real spend.

Exit code is non-zero if any real entry is stale, so this can gate a release.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "pricing" / "catalog.json"

#: The "test" provider is a deterministic fixture for the test suite, never
#: real spend. It carries no verified_at and needs no re-checking.
FIXTURE_PROVIDER = "test"

PRICING_PAGES = {
    "openai": "https://openai.com/api/pricing/",
    "anthropic": "https://www.anthropic.com/pricing#api",
    "bedrock": "https://aws.amazon.com/bedrock/pricing/",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
}


def main(staleness_days: int = 90) -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    today = datetime.now(UTC).date()

    real_models = [m for m in catalog["models"] if m["provider"] != FIXTURE_PROVIDER]
    if not real_models:
        print("No real-provider entries in the catalog; nothing to verify.")
        return 0

    print(f"Price catalog version: {catalog['version']}")
    print(f"Checking {len(real_models)} real-provider entries against a "
          f"{staleness_days}-day staleness window.\n")

    stale: list[str] = []
    for entry in sorted(real_models, key=lambda m: (m["provider"], m["model"])):
        key = f"{entry['provider']}::{entry['model']}"
        verified_at = entry.get("verified_at")
        if not verified_at:
            print(f"  [NO DATE]  {key} -- carries no verified_at at all")
            stale.append(key)
            continue

        age_days = (today - date.fromisoformat(verified_at)).days
        flag = "STALE" if age_days > staleness_days else "ok"
        print(
            f"  [{flag:>5}]  {key:<32} verified {verified_at} "
            f"({age_days}d ago)  in={entry['input_per_million']}/M "
            f"out={entry['output_per_million']}/M"
        )
        if flag == "STALE":
            stale.append(key)

    print()
    for provider, url in PRICING_PAGES.items():
        if any(m["provider"] == provider for m in real_models):
            print(f"  {provider:<10} {url}")

    if stale:
        print(
            f"\n{len(stale)} entr{'y is' if len(stale) == 1 else 'ies are'} "
            f"stale or unverified. Check the pricing pages above, update the "
            f"rates and verified_at in pricing/catalog.json, and bump the "
            f"catalog version -- never edit an already-published version, "
            f"since every ledger entry pins the version it was priced at."
        )
        return 1

    print("\nAll real-provider entries are within the staleness window.")
    return 0


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    sys.exit(main(days))
