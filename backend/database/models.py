from backend.database.db import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from sqlalchemy import DateTime, Uuid

import uuid

from datetime import datetime


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),
                                          primary_key=True,
                                          default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
