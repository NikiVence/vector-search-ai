from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase

from backend.config import DATABASE_URL



engine = create_async_engine(
    DATABASE_URL,
    echo=True,
)

session_maker = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_session() -> AsyncSession:
    async with session_maker() as session:
        yield session

class Base(DeclarativeBase):
    pass 