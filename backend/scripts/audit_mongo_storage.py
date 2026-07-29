from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.system.storage_audit import collect_storage_audit
from app.utils import serialize_doc


async def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MongoDB collection size and duplicate-schema audit")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--limit", type=int, default=20, help="number of largest collections to print")
    args = parser.parse_args()

    await connect_to_mongo()
    try:
        report = await collect_storage_audit(get_db())
    finally:
        await close_mongo_connection()

    if args.json:
        print(json.dumps(serialize_doc(report), ensure_ascii=False, indent=2))
        return
    _print_report(report, limit=max(1, args.limit))


def _print_report(report: dict[str, Any], *, limit: int) -> None:
    print(
        "MongoDB storage: "
        f"logical={_format_bytes(report['logical_size'])}, "
        f"storage={_format_bytes(report['storage_size'])}, "
        f"indexes={_format_bytes(report['index_size'])}"
    )
    print("collection\tdocuments\tlogical\tstorage\tindexes\tshare\tavg_doc")
    for item in report["collections"][:limit]:
        print(
            f"{item['name']}\t{item['count']}\t{_format_bytes(item['size'])}\t"
            f"{_format_bytes(item['storage_size'])}\t{_format_bytes(item['index_size'])}\t"
            f"{item['logical_share_percent']:.2f}%\t{_format_bytes(item['average_document_bytes'])}"
        )
    print("duplication_signals=" + json.dumps(serialize_doc(report["duplication_signals"]), ensure_ascii=False))


def _format_bytes(value: Any) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TiB"


if __name__ == "__main__":
    asyncio.run(main())
