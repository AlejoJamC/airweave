"""CRUD operations for entity definitions."""

from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity_definition import EntityDefinition
from app.schemas.entity_definition import EntityDefinitionCreate, EntityDefinitionUpdate

from ._base_system import CRUDBaseSystem


class CRUDEntityDefinition(
    CRUDBaseSystem[EntityDefinition, EntityDefinitionCreate, EntityDefinitionUpdate]
):
    """CRUD operations for Entity Definition."""

    async def get_multi_by_ids(
        self, db: AsyncSession, *, ids: List[UUID]
    ) -> List[EntityDefinition]:
        """Get multiple entity definitions by their IDs.

        Args:
            db (AsyncSession): The database session
            ids (List[UUID]): List of entity definition IDs to fetch

        Returns:
            List[EntityDefinition]: List of found entity definitions
        """
        result = await db.execute(select(self.model).where(self.model.id.in_(ids)))
        return list(result.unique().scalars().all())


entity_definition = CRUDEntityDefinition(EntityDefinition)
