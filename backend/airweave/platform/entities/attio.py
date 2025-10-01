"""Attio entity schemas.

Attio is a CRM platform that organizes data into Objects (Companies, People, Deals)
and Lists (custom collections). Each object/list contains Records with custom attributes.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from airweave.platform.entities._airweave_field import AirweaveField
from airweave.platform.entities._base import ChunkEntity


class AttioObjectEntity(ChunkEntity):
    """Schema for Attio Object (e.g., Companies, People, Deals).

    Objects are the core data types in Attio's CRM.
    """

    object_id: str = Field(..., description="ID of the object type (e.g., 'companies', 'people')")
    singular_noun: str = AirweaveField(
        ..., description="Singular name of the object (e.g., 'Company')", embeddable=True
    )
    plural_noun: str = AirweaveField(
        ..., description="Plural name of the object (e.g., 'Companies')", embeddable=True
    )
    api_slug: str = Field(..., description="API slug for the object")
    icon: Optional[str] = Field(None, description="Icon representing this object")
    created_at: Optional[datetime] = AirweaveField(
        None, description="When this object was created", embeddable=True, is_created_at=True
    )


class AttioListEntity(ChunkEntity):
    """Schema for Attio List.

    Lists are custom collections that can organize any type of record.
    """

    list_id: str = Field(..., description="Unique ID of the list")
    name: str = AirweaveField(..., description="Name of the list", embeddable=True)
    workspace_id: str = Field(..., description="ID of the workspace this list belongs to")
    parent_object: Optional[str] = AirweaveField(
        None, description="Parent object type if applicable", embeddable=True
    )
    created_at: Optional[datetime] = AirweaveField(
        None, description="When this list was created", embeddable=True, is_created_at=True
    )


class AttioRecordEntity(ChunkEntity):
    """Schema for Attio Record.

    Records are individual entries in Objects or Lists (e.g., a specific company, person, or deal).
    """

    record_id: str = Field(..., description="Unique ID of the record")
    object_id: Optional[str] = Field(None, description="ID of the object this record belongs to")
    list_id: Optional[str] = Field(None, description="ID of the list this record belongs to")
    parent_object_name: Optional[str] = AirweaveField(
        None, description="Name of the parent object/list", embeddable=True
    )

    # Dynamic attributes - these are the actual CRM data
    name: Optional[str] = AirweaveField(
        None, description="Name/title of the record", embeddable=True
    )
    description: Optional[str] = AirweaveField(
        None, description="Description of the record", embeddable=True
    )
    email_addresses: List[Dict[str, Any]] = AirweaveField(
        default_factory=list,
        description="Email addresses associated with this record",
        embeddable=True,
    )
    phone_numbers: List[Dict[str, Any]] = AirweaveField(
        default_factory=list,
        description="Phone numbers associated with this record",
        embeddable=True,
    )
    domains: List[str] = AirweaveField(
        default_factory=list, description="Domain names (for company records)", embeddable=True
    )
    categories: List[str] = AirweaveField(
        default_factory=list, description="Categories/tags for this record", embeddable=True
    )

    # Custom attributes stored as structured data
    attributes: Dict[str, Any] = AirweaveField(
        default_factory=dict,
        description="Custom attributes and their values",
        embeddable=True,
    )

    # Timestamps
    created_at: Optional[datetime] = AirweaveField(
        None, description="When this record was created", embeddable=True, is_created_at=True
    )
    updated_at: Optional[datetime] = AirweaveField(
        None,
        description="When this record was last updated",
        embeddable=True,
        is_updated_at=True,
    )

    # Metadata
    permalink_url: Optional[str] = Field(None, description="URL to view this record in Attio")


class AttioNoteEntity(ChunkEntity):
    """Schema for Attio Note.

    Notes are text entries attached to records for context and collaboration.
    """

    note_id: str = Field(..., description="Unique ID of the note")
    parent_record_id: str = Field(..., description="ID of the record this note is attached to")
    parent_object: Optional[str] = AirweaveField(
        None, description="Type of parent object", embeddable=True
    )

    # Note content
    title: Optional[str] = AirweaveField(None, description="Title of the note", embeddable=True)
    content: str = AirweaveField(..., description="Content of the note", embeddable=True)
    format: Optional[str] = Field(
        None, description="Format of the note (plaintext, markdown, etc.)"
    )

    # Author information
    author: Optional[Dict[str, Any]] = AirweaveField(
        None, description="User who created this note", embeddable=True
    )

    # Timestamps
    created_at: datetime = AirweaveField(
        ..., description="When this note was created", embeddable=True, is_created_at=True
    )
    updated_at: Optional[datetime] = AirweaveField(
        None, description="When this note was last updated", embeddable=True, is_updated_at=True
    )

    # Metadata
    permalink_url: Optional[str] = Field(None, description="URL to view this note in Attio")


class AttioCommentEntity(ChunkEntity):
    """Schema for Attio Comment.

    Comments are discussion threads on notes or records.
    """

    comment_id: str = Field(..., description="Unique ID of the comment")
    parent_note_id: Optional[str] = Field(
        None, description="ID of the note this comment is on (if applicable)"
    )
    parent_record_id: Optional[str] = Field(
        None, description="ID of the record this comment is on (if applicable)"
    )

    # Comment content
    text: str = AirweaveField(..., description="Text content of the comment", embeddable=True)

    # Author information
    author: Dict[str, Any] = AirweaveField(
        ..., description="User who created this comment", embeddable=True
    )

    # Timestamps
    created_at: datetime = AirweaveField(
        ..., description="When this comment was created", embeddable=True, is_created_at=True
    )

    # Metadata
    is_resolved: bool = Field(False, description="Whether this comment thread is resolved")
