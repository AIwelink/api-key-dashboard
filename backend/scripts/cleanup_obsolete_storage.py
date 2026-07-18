from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.system.storage_cleanup import cleanup_obsolete_storage
from app.utils import serialize_doc


CONFIRMATION = "DELETE-OBSOLETE-DATA"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove reconstructable duplicate fields and compact legacy capacity samples"
    )
    parser.add_argument("--execute", action="store_true", help="apply cleanup; default is read-only dry-run")
    parser.add_argument("--confirm", help=f"required with --execute: {CONFIRMATION}")
    parser.add_argument("--batch-size", type=int, default=200, help="legacy capacity conversion batch size")
    args = parser.parse_args()
    if args.execute and args.confirm != CONFIRMATION:
        parser.error(f"--execute requires --confirm {CONFIRMATION}")

    await connect_to_mongo()
    try:
        report = await cleanup_obsolete_storage(
            get_db(),
            execute=args.execute,
            batch_size=max(1, args.batch_size),
        )
    finally:
        await close_mongo_connection()
    print(json.dumps(serialize_doc(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
