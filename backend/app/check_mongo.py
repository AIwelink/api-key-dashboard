import asyncio

from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from app.config import get_mongodb_uri, get_settings
from app.database import close_mongo_connection, connect_to_mongo, get_db


async def main() -> None:
    settings = get_settings()
    print(f"MONGODB_DB={settings.mongodb_db}")
    print(f"MONGODB_USER_SET={bool(settings.mongodb_user)}")
    print(f"MONGODB_URI_SET={bool(settings.mongodb_uri)}")
    print(f"MONGODB_URI_PREVIEW={get_mongodb_uri().replace(settings.mongodb_password or '', '***')}")

    try:
        await connect_to_mongo()
        db = get_db()
        result = await db.command("ping")
        print(f"MongoDB ping ok: {result}")
    except OperationFailure as exc:
        print("MongoDB authentication failed.")
        print(f"Details: {exc}")
        raise SystemExit(1) from exc
    except ServerSelectionTimeoutError as exc:
        print("MongoDB server selection failed. Check host, port, firewall, and whether MongoDB is running.")
        print(f"Details: {exc}")
        raise SystemExit(1) from exc
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
