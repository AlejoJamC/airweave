"""Embedding model model."""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base
from app.platform.auth.schemas import AuthType

if TYPE_CHECKING:
    from app.models.connection import Connection


class EmbeddingModel(Base):
    """Embedding model model."""

    __tablename__ = "embedding_model"

    name: Mapped[str] = mapped_column(String, nullable=False)
    short_name: Mapped[str] = mapped_column(String, unique=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    auth_config_class: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    auth_type: Mapped[Optional[AuthType]] = mapped_column(SQLAlchemyEnum(AuthType), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Back-reference to connections
    connections: Mapped[list["Connection"]] = relationship(
        "Connection",
        primaryjoin="and_(foreign(Connection.short_name) == EmbeddingModel.short_name, "
        "Connection.integration_type == 'EMBEDDING_MODEL')",
        back_populates="embedding_model",
        lazy="noload",
        viewonly=True,
    )
