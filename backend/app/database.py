from collections.abc import AsyncIterator

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_mongodb_uri, get_settings


class Database:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


database = Database()


async def connect_to_mongo() -> None:
    settings = get_settings()
    database.client = AsyncIOMotorClient(get_mongodb_uri())
    database.db = database.client[settings.mongodb_db]


async def close_mongo_connection() -> None:
    if database.client is not None:
        database.client.close()
    database.client = None
    database.db = None


def get_db() -> AsyncIOMotorDatabase:
    if database.db is None:
        raise RuntimeError("MongoDB is not connected")
    return database.db


async def db_dependency() -> AsyncIterator[AsyncIOMotorDatabase]:
    yield get_db()
