from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forthatday_agent.infrastructure.catalog_store import CatalogStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Forthatday private catalog manifests.")
    parser.add_argument("database", type=Path)
    parser.add_argument("catalog_root", type=Path)
    parser.add_argument("verified_manifest", nargs="+", type=Path)
    parser.add_argument("--pending-manifest", type=Path)
    arguments = parser.parse_args()

    store = CatalogStore(arguments.database, catalog_root=arguments.catalog_root)
    imported = sum(store.import_verified_manifest(path) for path in arguments.verified_manifest)
    drafts = store.import_pending_drafts(arguments.pending_manifest) if arguments.pending_manifest else 0
    print({"imported_products": imported, "imported_drafts": drafts, **store.counts()})


if __name__ == "__main__":
    main()
