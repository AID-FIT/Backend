import asyncio

from app.db.models import Base
from app.db.session import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(main())
