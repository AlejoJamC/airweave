"""Entity schemas."""

import hashlib
import html as html_lib
import importlib
import json
import os
import re
import sys
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, create_model, model_validator


class DestinationAction(str, Enum):
    """Action for an entity."""

    INSERT = "insert"
    UPDATE = "update"
    KEEP = "keep"


class Breadcrumb(BaseModel):
    """Breadcrumb for tracking ancestry."""

    entity_id: str
    name: str
    type: str


class BaseEntity(BaseModel):
    """Base entity schema."""

    # Set in source connector
    entity_id: str = Field(
        ..., description="ID of the entity this entity represents in the source."
    )
    breadcrumbs: List[Breadcrumb] = Field(
        default_factory=list, description="List of breadcrumbs for this entity."
    )

    # Set in sync orchestrator
    db_entity_id: UUID = Field(
        default_factory=uuid4, description="Unique ID of the entity in the DB."
    )
    source_name: Optional[str] = Field(
        None, description="Name of the source this entity came from."
    )
    sync_id: Optional[UUID] = Field(None, description="ID of the sync this entity belongs to.")
    sync_job_id: Optional[UUID] = Field(
        None, description="ID of the sync job this entity belongs to."
    )
    url: Optional[str] = Field(None, description="URL to the original content, if applicable.")
    sync_metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional metadata for the sync."
    )

    parent_entity_id: Optional[str] = Field(
        None, description="ID of the parent entity in the source."
    )

    vector: Optional[List[float]] = Field(None, description="Vector representation of the entity.")
    chunk_index: Optional[int] = Field(
        None,
        description=(
            "Index of the chunk in the file, if applicable. "
            "Example: If a file is split into 2 chunks, "
            "the first chunk will have a chunk_index of 0, "
            "the second chunk will have a chunk_index of 1."
        ),
    )

    class Config:
        """Pydantic config."""

        from_attributes = True

    @model_validator(mode="after")
    def validate_timestamp_flags(self):
        """Validate that there is only one created_at and one updated_at field.

        Ensures that multiple fields don't claim to be the same timestamp type.
        """
        created_at_fields = []
        updated_at_fields = []

        for field_name, field_info in self.model_fields.items():
            if field_info.json_schema_extra and isinstance(field_info.json_schema_extra, dict):
                # Check for created_at flag
                if field_info.json_schema_extra.get("is_created_at"):
                    created_at_fields.append(field_name)

                # Check for updated_at flag
                if field_info.json_schema_extra.get("is_updated_at"):
                    updated_at_fields.append(field_name)

        # Check for duplicates
        errors = []
        if len(created_at_fields) > 1:
            errors.append(f"Multiple created_at fields: {created_at_fields}")
        if len(updated_at_fields) > 1:
            errors.append(f"Multiple updated_at fields: {updated_at_fields}")

        if errors:
            raise ValueError(
                f"Duplicate timestamp fields found in {self.__class__.__name__}: "
                + "; ".join(errors)
            )

        return self

    def hash(self) -> str:
        """Hash the entity using only content-relevant fields."""
        if getattr(self, "_hash", None):
            return self._hash

        # Define content-relevant fields (exclude metadata fields)
        metadata_fields = {
            "sync_job_id",
            "vector",
            "_hash",
            "db_entity_id",
            "source_name",
            "sync_id",
            "sync_metadata",
        }

        # Get field names from the model
        all_fields = set(self.model_fields.keys())
        content_fields = all_fields - metadata_fields

        # Extract only content fields
        data = {k: v for k, v in self.model_dump().items() if k in content_fields}

        # Use stable serialization
        def stable_serialize(obj):
            if isinstance(obj, dict):
                return {k: stable_serialize(v) for k, v in sorted(obj.items())}
            elif isinstance(obj, (list, tuple)):
                return [stable_serialize(x) for x in obj]
            elif isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            else:
                # Handle non-serializable types consistently
                return str(obj)

        # Create stable representation
        stable_data = stable_serialize(data)

        # Use canonical JSON encoding for consistent string representation
        json_str = json.dumps(stable_data, sort_keys=True, separators=(",", ":"))

        # Compute hash
        self._hash = hashlib.sha256(json_str.encode()).hexdigest()
        return self._hash

    def to_storage_dict(self, exclude_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Convert entity to a dictionary suitable for storage in vector databases.

        This method handles serialization of complex types (dicts, lists) to JSON strings,
        except for specific fields that should remain as objects (like breadcrumbs).

        Args:
            exclude_fields: Optional list of field names to exclude from serialization

        Returns:
            Dict with all fields properly serialized for storage
        """
        # Start with the full model dump
        data = self.model_dump()

        # Helper function to recursively clean nested structures
        def clean_nested_data(obj, exclude_set):
            if isinstance(obj, dict):
                # Remove excluded fields and recursively clean remaining values
                cleaned = {}
                for key, value in obj.items():
                    if key not in exclude_set:
                        cleaned[key] = clean_nested_data(value, exclude_set)
                return cleaned
            elif isinstance(obj, list):
                # Recursively clean each item in the list
                return [clean_nested_data(item, exclude_set) for item in obj]
            elif isinstance(obj, UUID):
                # Convert UUID objects to strings
                return str(obj)
            else:
                # Return primitive types as-is
                return obj

        # Create set of fields to exclude for faster lookup
        exclude_set = set(exclude_fields) if exclude_fields else set()

        # Recursively clean the data
        data = clean_nested_data(data, exclude_set)

        # Fields that should remain as objects and not be JSON serialized
        object_fields = {"breadcrumbs"}

        # Serialize complex types to JSON strings, except for specified object fields
        for key, value in data.items():
            if key not in object_fields and isinstance(value, (dict, list)):
                data[key] = json.dumps(value)

        return data


class ChunkEntity(BaseEntity):
    """Base class for entities that are storable and embeddable chunks of data."""

    # Persisted canonical text used for embedding and display
    embeddable_text: Optional[str] = Field(
        default=None,
        description=("Canonical, human-readable text built from the entity for embedding and UI."),
    )

    # Default fields to exclude when creating storage dict
    default_exclude_fields: List[str] = [
        "vector",  # Exclude the vector itself from the payload
        "db_entity_id",
        "sync_metadata",
        # parent_entity_id must remain for update/delete filters and grouping
        "default_exclude_fields",
        "_hash",
    ]

    # Global safeguard to cap embeddable text size (characters)
    embeddable_max_chars: ClassVar[int] = 12000

    def to_storage_dict(self, exclude_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Convert entity to a dictionary suitable for storage in vector databases.

        This implementation uses default exclusions to keep only the essential fields.

        Args:
            exclude_fields: Optional list of field names to exclude from serialization
                            (adds to default exclusions)

        Returns:
            Dict with minimal fields properly serialized for storage
        """
        # Combine default and provided exclusions
        all_exclusions = list(self.default_exclude_fields)
        if exclude_fields:
            all_exclusions.extend(exclude_fields)
        exclusions = list(set(all_exclusions))  # Remove duplicates

        # Use parent implementation with our combined exclusions
        return super().to_storage_dict(exclude_fields=exclusions)

    # -------- Embedding text construction (generic, source-agnostic) --------
    def build_embeddable_text(self) -> str:
        """Create a concise, high-signal markdown-like text for embedding and UI."""
        lines: List[str] = []
        # Header lines
        lines.extend(self._build_header_lines())
        # Title and breadcrumb
        used_title_key, title_line = self._build_title_line()
        if title_line:
            lines.append(title_line)
        breadcrumb = self._format_breadcrumbs()
        if breadcrumb:
            lines.append(f"* {breadcrumb}")
        # Annotated details
        lines.extend(self._build_annotated_lines(used_title_key))
        # Content snippet
        content_lines = self._build_content_lines()
        lines.extend(content_lines)
        # Finalize
        text = "\n".join(lines)
        text = self._normalize_spaces(text)
        if len(text) > self.embeddable_max_chars:
            text = text[: self.embeddable_max_chars]
        return text

    def _build_header_lines(self) -> List[str]:
        source = getattr(self, "source_name", None) or "unknown"
        src_line = f"* source: {self._normalize_spaces(str(source))}"
        type_readable = self._infer_entity_type_name(source)
        type_line = f"* type: {type_readable}"
        return [src_line, type_line]

    def _build_title_line(self) -> tuple[Optional[str], Optional[str]]:
        candidates = [
            ("md_title", getattr(self, "md_title", None)),
            ("name", getattr(self, "name", None)),
        ]
        title = next((t for _, t in candidates if isinstance(t, str) and t.strip()), None)
        if not title:
            return None, None
        used_key = next((k for k, v in candidates if v == title), None)
        return used_key, f"* name: {self._clean_text(title)}"

    def _build_annotated_lines(self, used_title_key: Optional[str]) -> List[str]:
        """Build annotated lines from fields marked as embeddable."""
        # Get embeddable fields from field metadata
        embeddable_field_names = self._get_embeddable_fields()

        # If no explicit embeddable fields, use defaults
        fields = (
            embeddable_field_names
            if embeddable_field_names
            else [
                "title",
                "summary",
                "description",
                "notes",
                "html_notes",
                "content",
                "text",
            ]
        )
        lines: List[str] = []
        for field_name in fields:
            if field_name in ("md_title", used_title_key):
                continue
            if not hasattr(self, field_name):
                continue
            value = getattr(self, field_name)
            if value is None:
                continue
            summarized = self._summarize_value(value)
            if not summarized:
                continue
            label = field_name.replace("_", " ")
            lines.append(f"* {label}: {summarized}")
        return lines

    def _get_embeddable_fields(self) -> List[str]:
        """Extract field names marked as embeddable from field metadata."""
        embeddable_fields = []
        for field_name, field_info in self.model_fields.items():
            # Check if field has embeddable metadata
            if field_info.json_schema_extra and isinstance(field_info.json_schema_extra, dict):
                if field_info.json_schema_extra.get("embeddable"):
                    embeddable_fields.append(field_name)
        return embeddable_fields

    def get_harmonized_timestamps(self) -> Dict[str, Any]:
        """Get harmonized timestamp values from fields marked with timestamp flags.

        Returns:
            Dict with 'created_at' and 'updated_at' keys mapped to actual field values
        """
        timestamps = {}
        for field_name, field_info in self.model_fields.items():
            if field_info.json_schema_extra and isinstance(field_info.json_schema_extra, dict):
                # Check for created_at flag
                if field_info.json_schema_extra.get("is_created_at"):
                    timestamps["created_at"] = getattr(self, field_name, None)
                # Check for updated_at flag
                if field_info.json_schema_extra.get("is_updated_at"):
                    timestamps["updated_at"] = getattr(self, field_name, None)
        return timestamps

    def _build_content_lines(self) -> List[str]:
        md_content = getattr(self, "md_content", None)
        if not isinstance(md_content, str) or not md_content.strip():
            return []
        content = self._clean_text(md_content)
        max_len = max(0, min(4000, self.embeddable_max_chars // 3))
        if len(content) > max_len:
            content = content[:max_len]
        return ["---", f"* content: {content}"]

    @staticmethod
    def _strip_html(value: str) -> str:
        # Remove HTML tags and unescape entities
        no_tags = re.sub(r"<[^>]+>", " ", value)
        return html_lib.unescape(no_tags)

    @classmethod
    def _clean_text(cls, value: str) -> str:
        cleaned = cls._strip_html(value)
        return cls._normalize_spaces(cleaned)

    @staticmethod
    def _normalize_spaces(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _summarize_value(cls, value: Any, max_items: int = 5) -> str:
        """Summarize a value into a short, readable string."""
        try:
            if isinstance(value, str):
                return cls._clean_text(value)
            if isinstance(value, dict):
                return cls._summarize_dict(value, max_items)
            if isinstance(value, list):
                return cls._summarize_list(value, max_items)
            return cls._normalize_spaces(str(value))
        except Exception:
            return ""

    @classmethod
    def _summarize_dict(cls, value: Dict[str, Any], max_items: int) -> str:
        # Prefer readable keys
        for key in ("name", "title", "text", "description"):
            if key in value and isinstance(value[key], str) and value[key].strip():
                return cls._clean_text(str(value[key]))
        # Fallback to a few key:value pairs
        items: List[str] = []
        count = 0
        for k, v in value.items():
            if count >= max_items:
                break
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                items.append(f"{k}:{str(v)}")
            elif isinstance(v, dict):
                sub = v.get("name") or v.get("title") or v.get("text")
                if sub:
                    items.append(f"{k}:{cls._normalize_spaces(str(sub))}")
            elif isinstance(v, list) and v:
                items.append(f"{k}:[{len(v)}]")
            count += 1
        return cls._normalize_spaces(", ".join(items))

    @classmethod
    def _summarize_list(cls, value: List[Any], max_items: int) -> str:
        pieces: List[str] = []
        for item in value[:max_items]:
            if isinstance(item, (str, int, float, bool)):
                pieces.append(cls._normalize_spaces(str(item)))
            elif isinstance(item, dict):
                sub = item.get("name") or item.get("title") or item.get("text")
                if sub:
                    pieces.append(cls._normalize_spaces(str(sub)))
        return ", ".join(pieces)

    def _format_breadcrumbs(self) -> str:
        if not getattr(self, "breadcrumbs", None):
            return ""
        try:
            path = " → ".join(
                [
                    f"{bc.type.capitalize()} {bc.name}" if bc.name else bc.type.capitalize()
                    for bc in self.breadcrumbs
                ]
            )
            return f"Context: {path}"
        except Exception:
            return ""

    def _infer_entity_type_name(self, source_name: Optional[str]) -> str:
        """Infer a readable type name from class name, e.g., AsanaTaskEntity -> Task.

        If source_name is present and matches the prefix (case-insensitive), remove it.
        Always strip trailing 'Entity'. Handle auto-generated unified chunks.
        """
        try:
            cls_name = self.__class__.__name__
            # strip suffix Entity
            if cls_name.endswith("Entity"):
                cls_name = cls_name[: -len("Entity")]
            # remove source prefix if matches
            if source_name:
                src = str(source_name).strip()
                if src and cls_name.lower().startswith(src.lower()):
                    cls_name = cls_name[len(src) :]
            # handle auto-generated unified chunk classes, e.g., AsanaFileUnifiedChunk
            if cls_name.endswith("UnifiedChunk"):
                base = cls_name[: -len("UnifiedChunk")]
                # Prefer concise labels
                if base.endswith("File"):
                    return "File"
                # Fallback to the base without suffix
                cls_name = base or cls_name
            # fallback
            readable = cls_name or self.__class__.__name__
            return readable or "Entity"
        except Exception:
            return "Entity"


class ParentEntity(BaseEntity):
    """Base class for entities that are parents of other entities."""

    pass


class PolymorphicEntity(ChunkEntity):
    """Base class for dynamically generated entities.

    This class serves as the base for entities that are created at runtime,
    particularly for database table entities where the schema is determined
    by the table structure.
    """

    __abstract__ = True
    table_name: str
    schema_name: Optional[str] = None
    primary_key_columns: List[str] = Field(default_factory=list)

    @classmethod
    def create_table_entity_class(
        cls,
        table_name: str,
        schema_name: Optional[str],
        columns: Dict[str, Any],
        primary_keys: List[str],
    ) -> Type["PolymorphicEntity"]:
        """Create a new entity class for a database table.

        Args:
            table_name: Name of the database table
            schema_name: Optional database schema name
            columns: Dictionary of column names to their types and metadata
            primary_keys: List of primary key column names

        Returns:
            A new entity class with fields matching the table structure
        """
        # Create field definitions for the new model
        fields: Dict[str, Any] = {
            "table_name": (str, Field(default=table_name)),
            "schema_name": (Optional[str], Field(default=schema_name)),
            "primary_key_columns": (List[str], Field(default_factory=lambda: primary_keys)),
        }

        # Add fields for each database column
        for col_name, col_info in columns.items():
            python_type = col_info.get("python_type", Any)
            if col_name == "id":
                col_name = "id_"
            fields[col_name] = (Optional[python_type], Field(default=None))

        # Create the new model class
        model_name = f"{table_name.title().replace('_', '')}TableEntity"
        return create_model(
            model_name,
            __base__=cls,
            **fields,
        )


# Registry to track which FileEntity subclasses have had their models created
_file_entity_models_created = set()


class FileEntity(BaseEntity):
    """Base schema for file entities."""

    file_id: str = Field(..., description="ID of the file in the source system")
    name: str = Field(..., description="Name of the file")
    mime_type: Optional[str] = Field(None, description="MIME type of the file")
    size: Optional[int] = Field(None, description="Size of the file in bytes")
    download_url: str = Field(..., description="URL to download the file")
    should_skip: bool = Field(False, description="Flag indicating if this file should be skipped")
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata about the file"
    )

    # File handling fields - set by file handler
    file_uuid: Optional[UUID] = Field(None, description="UUID assigned by the file manager")
    local_path: Optional[str] = Field(
        None, description="Temporary local path if file is downloaded"
    )
    checksum: Optional[str] = Field(None, description="File checksum/hash if available")
    total_size: Optional[int] = Field(None, description="Total size of the file in bytes")

    # Storage fields - set by storage manager
    storage_blob_name: Optional[str] = Field(
        None, description="Blob name in persistent storage (e.g., Azure)"
    )
    is_cached: bool = Field(False, description="Flag indicating if this file was loaded from cache")
    is_fully_processed: bool = Field(
        False,
        description="Flag indicating if this file was already fully processed (should be KEPT)",
    )

    def hash(self) -> str:
        """Hash the file entity.

        For files, we try the following strategies in order:
        1. If local_path is available, compute hash from actual file contents
        2. If checksum is available, use it as part of metadata hash
        3. Fall back to parent hash method using all metadata
        """
        if getattr(self, "_hash", None):
            return self._hash

        if self.local_path:
            # If we have the actual file, compute hash from its contents
            try:
                with open(self.local_path, "rb") as f:
                    content = f.read()
                    self._hash = hashlib.sha256(content).hexdigest()
                    return self._hash
            except Exception:
                # If file read fails, fall through to next method
                pass
        else:
            raise ValueError("File has no local path")

    @classmethod
    def create_parent_chunk_models(cls) -> Tuple[Type["ParentEntity"], Type["ChunkEntity"]]:
        """Create parent and chunk entity models for this file entity.

        This method dynamically generates two models:
        1. A parent model that inherits all fields from the source FileEntity subclass
           and represents the complete file metadata from the source system
        2. A chunk model that represents a chunk of the file's content with standardized
           fields for vector/graph DB storage

        Returns:
            A tuple of (ParentEntityClass, ChunkEntityClass)
        """
        # Get the class name prefix (e.g., "AsanaFile" from "AsanaFileEntity")
        class_name_prefix = cls.__name__.replace("Entity", "")

        # For parent, get all fields from the source FileEntity subclass
        parent_fields = {
            "number_of_chunks": (
                int,
                Field(default=0, description="Number of chunks of this file"),
            ),
        }
        for name, field in cls.model_fields.items():
            parent_fields[name] = (field.annotation, field)

        parent_model = create_model(
            f"{class_name_prefix}Parent", __base__=ParentEntity, **parent_fields
        )

        # Set module name to match the source entity's module
        parent_model.__module__ = cls.__module__

        # For chunk, create standardized fields for vector/graph DB storage
        chunk_fields = {
            "md_title": (Optional[str], Field(None, description="Title or heading of the chunk")),
            "md_content": (str, Field(..., description="The actual content of the chunk")),
            "md_type": (
                str,
                Field(..., description="Type of content (e.g., paragraph, table, list)"),
            ),
            "metadata": (
                Dict[str, Any],
                Field(default_factory=dict, description="Additional metadata about the chunk"),
            ),
            "md_position": (
                Optional[int],
                Field(None, description="Position of this chunk in the document"),
            ),
            "md_parent_title": (
                Optional[str],
                Field(None, description="Title of the parent document"),
            ),
            "md_parent_url": (
                Optional[str],
                Field(None, description="URL of the parent document if available"),
            ),
        }

        chunk_model = create_model(
            f"{class_name_prefix}Chunk", __base__=ChunkEntity, **chunk_fields
        )

        # Set module name to match the source entity's module
        chunk_model.__module__ = cls.__module__

        # Add docstrings to the models
        parent_model.__doc__ = (
            f"Parent entity for {class_name_prefix} files. Generated from {cls.__name__}."
        )
        chunk_model.__doc__ = (
            f"Chunk entity for {class_name_prefix} files. Generated from {cls.__name__}."
        )

        # Register models in the module they belong to
        module = sys.modules[cls.__module__]
        setattr(module, parent_model.__name__, parent_model)
        setattr(module, chunk_model.__name__, chunk_model)

        # Mark this class as having had its models created
        _file_entity_models_created.add(cls)

        return parent_model, chunk_model

    @classmethod
    def create_unified_chunk_model(cls) -> Type["ChunkEntity"]:
        """Create a unified Chunk model that carries full file metadata.

        This model is intended to replace the Parent/Chunk split. It inherits from
        ChunkEntity and includes:
        - All fields from the FileEntity subclass (full file metadata)
        - Standard chunk fields used for search (`md_*`, `metadata`, `md_position`)
        """
        # Get the class name prefix (e.g., "AsanaFile" from "AsanaFileEntity")
        class_name_prefix = cls.__name__.replace("Entity", "")

        # Standard chunk fields
        chunk_fields = {
            "md_title": (Optional[str], Field(None, description="Title or heading of the chunk")),
            "md_content": (str, Field(..., description="The actual content of the chunk")),
            "md_type": (
                str,
                Field(..., description="Type of content (e.g., paragraph, table, list)"),
            ),
            "metadata": (
                Dict[str, Any],
                Field(default_factory=dict, description="Additional metadata about the chunk"),
            ),
            "md_position": (
                Optional[int],
                Field(None, description="Position of this chunk in the document"),
            ),
            "md_parent_title": (
                Optional[str],
                Field(None, description="Title of the parent document"),
            ),
            "md_parent_url": (
                Optional[str],
                Field(None, description="URL of the parent document if available"),
            ),
        }

        # Include all fields from the FileEntity subclass so each chunk is "beefy"
        for name, field in cls.model_fields.items():
            # Don't duplicate BaseEntity fields already present via inheritance
            if name in BaseEntity.model_fields:
                continue
            chunk_fields[name] = (field.annotation, field)

        unified_chunk_model = create_model(
            f"{class_name_prefix}UnifiedChunk", __base__=ChunkEntity, **chunk_fields
        )

        # Set module name to match the source entity's module
        unified_chunk_model.__module__ = cls.__module__

        # Docstring
        unified_chunk_model.__doc__ = (
            f"Unified chunk entity for {class_name_prefix} files. Generated from {cls.__name__}."
        )

        # Register the model in its module for importability/debugging
        module = sys.modules[cls.__module__]
        setattr(module, unified_chunk_model.__name__, unified_chunk_model)

        return unified_chunk_model


class CodeFileEntity(ChunkEntity):
    """Base schema for code file entities."""

    # Basic entity fields
    source_name: str = Field(..., description="Source name")
    name: str = Field(..., description="File name")

    # File specific fields
    file_id: str = Field(..., description="Unique ID of the file")
    mime_type: Optional[str] = Field(None, description="MIME type of the file")
    size: int = Field(..., description="Size of the file in bytes")

    # Code specific fields
    language: Optional[str] = Field(None, description="Programming language of the file")
    line_count: Optional[int] = Field(None, description="Number of lines in the file")
    path_in_repo: str = Field(..., description="Path of the file within the repository")
    last_modified: Optional[datetime] = Field(None, description="Last modification timestamp")
    commit_id: Optional[str] = Field(None, description="Last commit ID that modified this file")
    repo_name: str = Field(..., description="Name of the repository containing this file")
    repo_owner: str = Field(..., description="Owner of the repository")

    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional metadata about the file"
    )

    summary: Optional[str] = Field(None, description="Summary of the file")

    # Content and navigation
    url: str = Field(..., description="URL to view the file")
    content: Optional[str] = Field(None, description="File content if available")
    breadcrumbs: List[Breadcrumb] = Field(
        default_factory=list, description="Breadcrumb navigation path"
    )


class WebEntity(BaseEntity):
    """Entity representing a web page to be crawled."""

    url: str = Field(..., description="URL to crawl")
    title: Optional[str] = Field(None, description="Page title if known")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    is_fully_processed: bool = Field(
        False,
        description="Flag indicating if this entity was already fully processed (should be KEPT)",
    )


def ensure_file_entity_models():
    """Ensure that all FileEntity subclasses have their parent and chunk models created.

    This function can be called at runtime to make sure all FileEntity subclasses
    have had their parent and chunk models created and registered in their modules.
    """
    # First, proactively import all entity modules
    entity_files = [
        f
        for f in os.listdir("airweave/platform/entities")
        if f.endswith(".py") and not f.startswith("__")
    ]

    for entity_file in entity_files:
        module_name = entity_file[:-3]  # Remove .py extension
        full_module_name = f"airweave.platform.entities.{module_name}"
        try:
            importlib.import_module(full_module_name)
        except Exception as e:
            print(f"Error importing entity module {full_module_name}: {e}")

    # Now check all loaded modules for FileEntity subclasses
    for _, module in list(sys.modules.items()):
        # Skip modules that don't have __dict__ attribute
        if not hasattr(module, "__dict__"):
            continue

        if not module.__name__.startswith("airweave.platform"):
            continue

        # Look for FileEntity subclasses in the module
        for _, cls in list(module.__dict__.items()):
            # Check if it's a class and a subclass of FileEntity (but not FileEntity itself)
            if (
                isinstance(cls, type)
                and issubclass(cls, FileEntity)
                and cls is not FileEntity
                and cls not in _file_entity_models_created
            ):
                try:
                    # Create parent and chunk models
                    parent_model, chunk_model = cls.create_parent_chunk_models()
                    print(
                        "Runtime: Auto-generated parent and chunk models for "
                        f"{cls.__name__} in {cls.__module__}"
                    )
                except Exception as e:
                    print(f"Runtime: Error creating models for {cls.__name__}: {e}")
