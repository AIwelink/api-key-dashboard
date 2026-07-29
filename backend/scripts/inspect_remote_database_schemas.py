from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.system.database_schema import inspect_all_configured_database_schemas
from app.utils import serialize_doc


async def main() -> None:
    await connect_to_mongo()
    try:
        result = await inspect_all_configured_database_schemas(get_db())
        print(json.dumps(serialize_doc(result), ensure_ascii=False, indent=2))
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
