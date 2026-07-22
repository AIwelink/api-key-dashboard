from __future__ import annotations

import asyncio
import json

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.system.growth_database_settings import initialize_growth_database


SYSTEM_ACTOR = {
    "_id": "system:growth-database-initializer",
    "name": "Growth database initializer",
}


async def main() -> None:
    await connect_to_mongo()
    try:
        result = await initialize_growth_database(get_db(), actor=SYSTEM_ACTOR)
        print(json.dumps(result, ensure_ascii=False, default=str))
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
