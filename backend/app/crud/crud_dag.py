"""CRUD operations for DAG models."""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import schemas
from app.crud._base import CRUDBase
from app.models.dag import (
    DagEdge,
    DagNode,
    SyncDagDefinition,
)
from app.schemas.dag import (
    SyncDagDefinitionCreate,
    SyncDagDefinitionUpdate,
)


class CRUDSyncDagDefinition(
    CRUDBase[SyncDagDefinition, SyncDagDefinitionCreate, SyncDagDefinitionUpdate]
):
    """CRUD operations for SyncDagDefinition."""

    async def create_with_nodes_and_edges(
        self,
        db: AsyncSession,
        *,
        obj_in: SyncDagDefinitionCreate,
        current_user: schemas.User,
    ) -> SyncDagDefinition:
        """Create a DAG with its nodes and edges."""
        # Create the base DAG object
        db_obj = SyncDagDefinition(
            name=obj_in.name,
            description=obj_in.description,
            sync_id=obj_in.sync_id,
            organization_id=current_user.organization_id,
            created_by_email=current_user.email,
            modified_by_email=current_user.email,
        )
        db.add(db_obj)
        await db.flush()  # Flush to get the ID

        # Create nodes
        for node_in in obj_in.nodes:
            node_data = node_in.model_dump()
            db_node = DagNode(
                **node_data,
                dag_id=db_obj.id,
                organization_id=current_user.organization_id,
                created_by_email=current_user.email,
                modified_by_email=current_user.email,
            )
            db.add(db_node)
        await db.flush()

        # Create edges
        for edge_in in obj_in.edges:
            db_edge = DagEdge(
                from_node_id=edge_in.from_node_id,
                to_node_id=edge_in.to_node_id,
                dag_id=db_obj.id,
                organization_id=current_user.organization_id,
                created_by_email=current_user.email,
                modified_by_email=current_user.email,
            )
            db.add(db_edge)

        await db.commit()
        await db.refresh(db_obj)

        # Reload with relationships
        result = await db.execute(
            select(SyncDagDefinition)
            .where(SyncDagDefinition.id == db_obj.id)
            .options(
                selectinload(SyncDagDefinition.nodes),
                selectinload(SyncDagDefinition.edges),
            )
        )
        return result.scalar_one()

    async def update_with_nodes_and_edges(
        self,
        db: AsyncSession,
        *,
        db_obj: SyncDagDefinition,
        obj_in: SyncDagDefinitionUpdate,
        current_user: schemas.User,
    ) -> SyncDagDefinition:
        """Update a DAG with its nodes and edges."""
        # Update DAG fields
        db_obj = await self.update(db, db_obj=db_obj, obj_in=obj_in, current_user=current_user)

        # If nodes provided, replace all nodes
        if obj_in.nodes is not None:
            # Delete existing nodes (cascade will handle edges)
            await db.execute(select(DagNode).where(DagNode.dag_id == db_obj.id).delete())
            # Create new nodes
            for node_in in obj_in.nodes:
                node_data = node_in.model_dump()
                db_node = DagNode(
                    **node_data,
                    dag_id=db_obj.id,
                    organization_id=current_user.organization_id,
                    created_by_email=current_user.email,
                    modified_by_email=current_user.email,
                )
                db.add(db_node)
            await db.flush()

        # If edges provided, replace all edges
        if obj_in.edges is not None:
            # Delete existing edges
            await db.execute(select(DagEdge).where(DagEdge.dag_id == db_obj.id).delete())
            # Create new edges
            for edge_in in obj_in.edges:
                db_edge = DagEdge(
                    from_node_id=edge_in.from_node_id,
                    to_node_id=edge_in.to_node_id,
                    dag_id=db_obj.id,
                    organization_id=current_user.organization_id,
                    created_by_email=current_user.email,
                    modified_by_email=current_user.email,
                )
                db.add(db_edge)

        await db.commit()
        await db.refresh(db_obj)

        # Reload with relationships
        result = await db.execute(
            select(SyncDagDefinition)
            .where(SyncDagDefinition.id == db_obj.id)
            .options(
                selectinload(SyncDagDefinition.nodes),
                selectinload(SyncDagDefinition.edges),
            )
        )
        return result.scalar_one()

    async def get_by_sync_id(
        self,
        db: AsyncSession,
        *,
        sync_id: UUID,
        current_user: schemas.User,
    ) -> Optional[SyncDagDefinition]:
        """Get a DAG by sync ID."""
        result = await db.execute(
            select(SyncDagDefinition)
            .where(
                SyncDagDefinition.sync_id == sync_id,
                SyncDagDefinition.organization_id == current_user.organization_id,
            )
            .options(
                selectinload(SyncDagDefinition.nodes),
                selectinload(SyncDagDefinition.edges),
            )
        )
        return result.scalar_one_or_none()


sync_dag_definition = CRUDSyncDagDefinition(SyncDagDefinition)
