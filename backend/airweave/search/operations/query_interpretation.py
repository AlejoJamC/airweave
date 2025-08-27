"""Query interpretation operation.

This operation uses LLM to interpret natural language queries and extract
structured filters, enabling users to filter results without knowing the
exact filter syntax.
"""

from typing import Any, Dict, List, Optional, Set

from airweave.search.operations.base import SearchOperation


class QueryInterpretation(SearchOperation):
    """Interprets natural language to extract filters using LLM.

    This operation analyzes the user's query to identify filtering
    criteria like data sources, time ranges, status values, etc.
    It dynamically discovers available fields from the collection's
    entity definitions and generates appropriate Qdrant filters.

    Example:
        Input: "open tickets in Asana from last week"
        Extracts:
            - source_name: "Asana"
            - status: "open"
            - time_range: last 7 days
        Refined query: "tickets"
    """

    def __init__(self, model: str = "gpt-4o-mini", confidence_threshold: float = 0.75):
        """Initialize query interpretation.

        Args:
            model: OpenAI model to use for extraction
            confidence_threshold: Minimum confidence to apply extracted filters (0.75 default)
        """
        self.model = model
        self.confidence_threshold = confidence_threshold

    @property
    def name(self) -> str:
        """Operation name."""
        return "query_interpretation"

    @property
    def optional(self) -> bool:
        """This operation is optional - search continues if it fails."""
        return True

    async def execute(self, context: Dict[str, Any]) -> None:
        """Extract filters from the query using LLM.

        Reads from context:
            - query: Search query to analyze
            - config: SearchConfig
            - logger: For logging
            - openai_api_key: API key for OpenAI
            - db: Database session for discovering fields

        Writes to context:
            - filter: Generated Qdrant filter (if successful and confident)
            - query: Refined query with filter terms removed
        """
        query = context["query"]
        logger = context["logger"]
        openai_api_key = context.get("openai_api_key")
        db = context.get("db")
        collection_id = context["config"].collection_id

        if not openai_api_key:
            logger.debug(f"[{self.name}] No OpenAI API key, skipping filter extraction")
            return

        logger.info(f"[{self.name}] Analyzing query for filters: {query[:100]}...")

        # Discover available fields from the collection's entities
        available_fields = await self._discover_available_fields(
            db, collection_id, logger, context.get("ctx")
        )

        try:
            # Define Pydantic models for structured output
            filter_models = self._create_filter_models()
            ExtractedFilters = filter_models["ExtractedFilters"]

            # Create OpenAI client and get extraction
            extracted = await self._get_llm_extraction(
                openai_api_key, query, available_fields, ExtractedFilters, logger
            )

            if not extracted:
                return

            # Check confidence threshold
            if not self._check_confidence(extracted, logger):
                return

            # Process and validate extracted filters
            validated_conditions = self._process_extracted_filters(
                extracted, available_fields, logger
            )

            # Apply filters if valid
            self._apply_filters(validated_conditions, extracted, context, logger)

        except Exception as e:
            # Since this is optional, we just log and continue
            logger.warning(f"[{self.name}] Failed: {e}")
            # Don't re-raise - let search continue without interpreted filters

    def _create_filter_models(self) -> Dict[str, Any]:
        """Create Pydantic models for filter extraction."""
        from pydantic import BaseModel, Field

        class MatchValue(BaseModel):
            value: str | int | float | bool

            model_config = {"extra": "forbid"}

        class MatchAny(BaseModel):
            any: List[str | int | float | bool]

            model_config = {"extra": "forbid"}

        class RangeObject(BaseModel):
            gte: Optional[str | float | int] = None
            gt: Optional[str | float | int] = None
            lte: Optional[str | float | int] = None
            lt: Optional[str | float | int] = None

            model_config = {"extra": "forbid"}

        class FilterCondition(BaseModel):
            key: str = Field(
                description="Qdrant field key (e.g., 'source_name', 'status', 'created_at')"
            )
            match: Optional[MatchValue | MatchAny] = Field(
                default=None, description="Either a value match or any-of match"
            )
            range: Optional[RangeObject] = Field(
                default=None,
                description=(
                    "Range object with gte/gt/lte/lt for numeric or ISO8601 datetime strings"
                ),
            )

            model_config = {"extra": "forbid"}

        class ExtractedFilters(BaseModel):
            """Filters extracted from natural language query."""

            filters: List[FilterCondition] = Field(
                default_factory=list, description="List of Qdrant filter conditions"
            )
            confidence: float = Field(
                ge=0.0, le=1.0, description="Confidence score for the extracted filters (0-1)"
            )
            refined_query: str = Field(
                description="Query with filter terms removed for better semantic search"
            )
            reasoning: str = Field(description="Explanation of what was extracted and why")

            model_config = {"extra": "forbid"}

        return {
            "MatchValue": MatchValue,
            "MatchAny": MatchAny,
            "RangeObject": RangeObject,
            "FilterCondition": FilterCondition,
            "ExtractedFilters": ExtractedFilters,
        }

    async def _get_llm_extraction(
        self,
        openai_api_key: str,
        query: str,
        available_fields: Dict,
        ExtractedFilters: Any,
        logger: Any,
    ) -> Optional[Any]:
        """Get filter extraction from LLM."""
        from openai import AsyncOpenAI

        # Create OpenAI client
        client = AsyncOpenAI(api_key=openai_api_key)

        # Create prompt with available fields and explicit source list
        system_prompt = self._build_system_prompt(available_fields)
        if logger.isEnabledFor(10):  # DEBUG
            logger.debug(f"[{self.name}] Prepared system prompt (chars={len(system_prompt)})")
        user_prompt = f"Extract filters from this search query: {query}"

        # Get extraction from LLM
        response = await client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ExtractedFilters,
            temperature=0.3,
        )

        if response.choices[0].message.parsed:
            extracted = response.choices[0].message.parsed
            if logger.isEnabledFor(10):
                try:
                    logger.debug(
                        f"[{self.name}] Raw structured extraction: {extracted.model_dump()}"
                    )
                except Exception:
                    pass
            logger.info(
                f"[{self.name}] Extracted filters with confidence {extracted.confidence:.2f}: "
                f"{extracted.reasoning[:100]}..."
            )
            return extracted
        return None

    def _check_confidence(self, extracted: Any, logger: Any) -> bool:
        """Check if extraction confidence meets threshold."""
        if extracted.confidence < self.confidence_threshold:
            logger.info(
                f"[{self.name}] Confidence {extracted.confidence:.2f} below threshold "
                f"{self.confidence_threshold}, not applying filters"
            )
            return False
        return True

    def _process_extracted_filters(
        self, extracted: Any, available_fields: Dict, logger: Any
    ) -> List[Dict[str, Any]]:
        """Process and validate extracted filter conditions."""
        if not extracted.filters:
            return []

        # Convert FilterCondition objects to plain dicts
        raw_conditions = self._convert_to_raw_conditions(extracted.filters, logger)

        # Validate conditions against available fields
        validated_conditions = self._validate_conditions(raw_conditions, available_fields, logger)

        return validated_conditions

    def _convert_to_raw_conditions(self, filters: List, logger: Any) -> List[Dict[str, Any]]:
        """Convert FilterCondition objects to plain dictionaries."""
        raw_conditions: List[Dict[str, Any]] = []
        for cond in filters:
            cond_dict: Dict[str, Any] = {"key": cond.key}
            if cond.match is not None:
                # cond.match can be MatchValue or MatchAny
                try:
                    cond_dict["match"] = cond.match.model_dump()
                except Exception:
                    cond_dict["match"] = {"value": getattr(cond.match, "value", None)}
            if cond.range is not None:
                try:
                    cond_dict["range"] = cond.range.model_dump(exclude_none=True)
                except Exception:
                    pass
            raw_conditions.append(cond_dict)

        if logger.isEnabledFor(10):
            logger.debug(f"[{self.name}] Raw extracted conditions: {raw_conditions}")

        return raw_conditions

    def _validate_conditions(
        self, raw_conditions: List[Dict], available_fields: Dict, logger: Any
    ) -> List[Dict[str, Any]]:
        """Validate conditions against available fields and sources."""
        # Build allowed keys and sources
        allowed_keys: Set[str] = set()
        for src, fields in available_fields.items():
            if src == "common":
                allowed_keys.update(fields)
            else:
                allowed_keys.update(fields)
        allowed_sources: Set[str] = {s for s in available_fields.keys() if s != "common"}

        validated_conditions: List[Dict[str, Any]] = []
        for fc in raw_conditions:
            key = fc.get("key")
            if key not in allowed_keys:
                logger.debug(f"[{self.name}] Dropping condition with unknown key: {key}")
                continue

            # Special handling for source_name field (accept both plain and namespaced keys)
            if key in ("source_name", "airweave_system_metadata.source_name"):
                fc = self._validate_source_name(fc, allowed_sources, logger)
                if not fc:
                    continue

            # If both match and range exist, prefer range for time/numeric queries
            if fc.get("range"):
                fc.pop("match", None)

            validated_conditions.append(fc)

        if logger.isEnabledFor(10):
            logger.debug(f"[{self.name}] Validated conditions: {validated_conditions}")

        return validated_conditions

    def _validate_source_name(
        self, condition: Dict, allowed_sources: Set[str], logger: Any
    ) -> Optional[Dict]:
        """Validate source_name condition against allowed sources."""
        if "match" not in condition or not isinstance(condition["match"], dict):
            return condition

        match_obj = condition["match"]
        # Normalize to set of candidate values
        candidates: List[str] = []
        if "value" in match_obj:
            candidates = [str(match_obj["value"])]
        elif "any" in match_obj and isinstance(match_obj["any"], list):
            candidates = [str(v) for v in match_obj["any"]]

        # Only keep candidates that match available sources exactly
        filtered = self._filter_candidates_against_sources(candidates, allowed_sources)
        # Try basic normalization (display -> short_name style): lowercase, spaces -> underscores
        if not filtered:
            norm_candidates = [self._safe_lower_underscore(v) for v in candidates]
            filtered = self._filter_candidates_against_sources(norm_candidates, allowed_sources)

        if not filtered:
            logger.debug(
                f"[{self.name}] Dropping source_name condition with unknown sources: {candidates}"
            )
            return None

        # Canonicalize to lowercase short_names when available and de-duplicate
        canonical = self._canonicalize_source_values(filtered, allowed_sources)

        # Rewrite match with canonicalized values
        if len(canonical) == 1:
            condition["match"] = {"value": canonical[0]}
        else:
            condition["match"] = {"any": canonical}

        return condition

    def _safe_lower_underscore(self, value: str) -> str:
        """Lowercase and replace spaces with underscores; fallback to original on error."""
        try:
            return value.strip().lower().replace(" ", "_")
        except Exception:
            return value

    def _filter_candidates_against_sources(
        self, candidates: List[str], allowed_sources: Set[str]
    ) -> List[str]:
        """Filter candidate source names against allowed sources."""
        return [v for v in candidates if v in allowed_sources]

    def _canonicalize_source_values(
        self, values: List[str], allowed_sources: Set[str]
    ) -> List[str]:
        """Prefer lowercase short_names when available and de-duplicate preserving order."""
        canonical: List[str] = []
        for v in values:
            try:
                lower_v = v.lower()
            except Exception:
                lower_v = v
            if isinstance(v, str) and isinstance(lower_v, str) and lower_v in allowed_sources:
                canonical.append(lower_v)
            else:
                canonical.append(v)

        seen: Set[str] = set()
        dedup: List[str] = []
        for v in canonical:
            if v not in seen:
                dedup.append(v)
                seen.add(v)
        return dedup

    def _apply_filters(
        self, validated_conditions: List[Dict], extracted: Any, context: Dict, logger: Any
    ) -> None:
        """Apply validated filters to the context."""
        qdrant_filter = self._build_qdrant_filter(validated_conditions)
        if qdrant_filter:
            context["filter"] = qdrant_filter
            context["query"] = extracted.refined_query
            logger.info(
                f"[{self.name}] Applied {len(extracted.filters)} filter conditions, "
                f"refined query: '{extracted.refined_query[:50]}...'"
            )
            if logger.isEnabledFor(10):
                logger.debug(f"[{self.name}] Final Qdrant filter: {qdrant_filter}")
        else:
            logger.info(f"[{self.name}] No filters to apply despite extraction")

    async def _discover_available_fields(
        self, db: Any, collection_id: str, logger: Any, ctx: Any | None = None
    ) -> Dict[str, Dict[str, str]]:
        """Discover available fields from collection's entity definitions.

        Args:
            db: Database session
            collection_id: Collection ID
            logger: Logger for debugging
            ctx: API context for additional logging

        Returns:
            Dict mapping entity types to their available fields
        """
        # Initialize with common fields
        available_fields = self._get_common_fields()

        try:
            # Get source connections for this collection
            source_connections = await self._get_source_connections(db, collection_id, logger, ctx)

            # Process each source connection
            await self._process_source_connections(db, source_connections, available_fields, logger)

            # Augment with PostgreSQL field catalog if present
            try:
                await self._augment_with_pg_catalog(
                    db=db,
                    source_connections=source_connections,
                    available_fields=available_fields,
                    logger=logger,
                    ctx=ctx,
                )
            except Exception as e:
                logger.debug(f"[{self.name}] Failed to augment with PG catalog: {e}")

            logger.debug(
                f"[{self.name}] Discovered fields for {len(available_fields)} sources: "
                f"{available_fields}"
            )

        except Exception as e:
            logger.warning(f"[{self.name}] Could not discover fields: {e}")

        return available_fields

    async def _augment_with_pg_catalog(
        self,
        db: Any,
        source_connections: List,
        available_fields: Dict[str, Dict[str, str]],
        logger: Any,
        ctx: Any | None,
    ) -> None:
        """Augment available fields with Postgres catalog for matching connections.

        Adds entries under the display source key "PostgreSQL" in the form
        "table.column" with concise type hints. Also adds a lowercase alias key
        "postgresql" (empty) so source_name filters can match payload values if needed.
        """
        if not source_connections or ctx is None:
            return

        pg_conns = self._filter_pg_connections(source_connections)
        if not pg_conns:
            return

        organization_id = self._get_organization_id(ctx)
        if not organization_id:
            return

        self._ensure_pg_keys_exist(available_fields)

        for sc in pg_conns:
            sc_id = getattr(sc, "id", None)
            if not sc_id:
                continue
            try:
                tables = await self._fetch_pg_tables(db, organization_id, str(sc_id))
                added_count = self._add_pg_fields_from_tables(tables, available_fields)
                if added_count:
                    logger.debug(
                        f"[{self.name}] Loaded {added_count} Postgres fields from catalog "
                        f"for source_connection={sc_id}"
                    )
            except Exception as e:
                logger.debug(f"[{self.name}] PG catalog lookup error: {e}")

    def _filter_pg_connections(self, source_connections: List) -> List:
        """Return connections whose short_name is 'postgresql'."""
        return [sc for sc in source_connections if getattr(sc, "short_name", None) == "postgresql"]

    def _get_organization_id(self, ctx: Any | None) -> Optional[str]:
        """Safely extract organization id from ctx."""
        if ctx is None:
            return None
        return getattr(getattr(ctx, "organization", None), "id", None)

    def _ensure_pg_keys_exist(self, available_fields: Dict[str, Dict[str, str]]) -> None:
        """Ensure 'PostgreSQL' and 'postgresql' keys exist in available_fields."""
        if "PostgreSQL" not in available_fields:
            available_fields["PostgreSQL"] = {}
        if "postgresql" not in available_fields:
            available_fields["postgresql"] = {}

    async def _fetch_pg_tables(
        self, db: Any, organization_id: str, source_connection_id: str
    ) -> List:
        """Fetch catalog tables with columns for a specific connection."""
        # Lazy import to avoid circular deps
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from airweave.models.pg_field_catalog import (
            PgFieldCatalogTable,
        )

        stmt = (
            select(PgFieldCatalogTable)
            .where(
                PgFieldCatalogTable.organization_id == organization_id,
                PgFieldCatalogTable.source_connection_id == source_connection_id,
            )
            .options(selectinload(PgFieldCatalogTable.columns))
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    def _add_pg_fields_from_tables(
        self, tables: List, available_fields: Dict[str, Dict[str, str]]
    ) -> int:
        """Add table.column entries into available_fields['PostgreSQL']; return count added."""
        added_count = 0
        for t in tables or []:
            table_name = getattr(t, "table_name", None)
            if not table_name:
                continue
            for c in getattr(t, "columns", None) or []:
                col_name = getattr(c, "column_name", None)
                if not col_name:
                    continue
                dt = getattr(c, "data_type", None) or getattr(c, "udt_name", None) or ""
                key = f"{table_name}.{col_name}"
                available_fields["PostgreSQL"][key] = f"Postgres column ({dt})"
                added_count += 1
        return added_count

    def _get_common_fields(self) -> Dict[str, Dict[str, str]]:
        return {
            "common": {
                "entity_id": "ID of the entity in the source system (string)",
                "airweave_system_metadata.source_name": (  # Changed
                    "Name of the source connector this data came from (string, case-sensitive)"
                ),
                "airweave_system_metadata.sync_id": (  # Changed
                    "Internal sync identifier (UUID, useful for debugging; "
                    "rarely used for filtering)"
                ),
                "airweave_system_metadata.sync_job_id": (
                    "Internal sync job identifier (UUID; rarely used for filtering)"
                ),
                "url": "Canonical URL to view the item in the source system (string)",
                "parent_entity_id": "ID of the parent entity in the source (string)",
                "chunk_index": "Index of the chunk within a document (integer)",
                "airweave_system_metadata.airweave_created_at": (
                    "Timestamp of when the entity was created in Airweave (ISO8601 datetime string)"
                ),
                "airweave_system_metadata.airweave_updated_at": (
                    "Harmonized update timestamp for decay calculations (ISO8601 datetime string)"
                ),
            }
        }

    async def _get_source_connections(
        self, db: Any, collection_id: str, logger: Any, ctx: Any | None
    ) -> List:
        """Get source connections for a collection."""
        from airweave import crud
        from airweave.core.source_connection_service import source_connection_service

        source_connections = []
        if ctx is not None:
            try:
                # First try with the given identifier as readable_id
                source_connections = (
                    await source_connection_service.get_source_connections_by_collection(
                        db=db, collection=collection_id, ctx=ctx
                    )
                )
            except Exception as e:
                logger.debug(f"[{self.name}] First attempt to list source connections failed: {e}")

            # If still empty, try resolving readable_id from UUID
            if not source_connections:
                source_connections = await self._resolve_from_uuid(
                    db, collection_id, logger, ctx, crud, source_connection_service
                )

        return source_connections

    async def _resolve_from_uuid(
        self, db: Any, collection_id: str, logger: Any, ctx: Any, crud: Any, service: Any
    ) -> List:
        """Resolve source connections from collection UUID."""
        try:
            from uuid import UUID

            collection_obj = await crud.collection.get(db, id=UUID(collection_id), ctx=ctx)
            if collection_obj and hasattr(collection_obj, "readable_id"):
                source_connections = await service.get_source_connections_by_collection(
                    db=db, collection=collection_obj.readable_id, ctx=ctx
                )
                logger.debug(
                    f"[{self.name}] Resolved collection "
                    f"readable_id='{collection_obj.readable_id}' and fetched "
                    f"{len(source_connections)} source connections"
                )
                return source_connections
        except Exception as e:
            logger.debug(f"[{self.name}] Could not resolve collection readable_id from UUID: {e}")
        return []

    async def _process_source_connections(
        self, db: Any, source_connections: List, available_fields: Dict, logger: Any
    ) -> None:
        """Process source connections to extract available fields."""
        from airweave import crud

        for conn in source_connections:
            # SourceConnectionListItem provides 'short_name'
            short_name = getattr(conn, "short_name", None)
            if not short_name:
                continue

            source = await crud.source.get_by_short_name(db, short_name=short_name)
            if source:
                await self._process_single_source(db, source, available_fields, logger)

    async def _process_single_source(
        self, db: Any, source: Any, available_fields: Dict, logger: Any
    ) -> None:
        """Process a single source to extract its fields."""
        from airweave import crud, schemas
        from airweave.platform.locator import resource_locator

        source_name = source.name
        if source_name not in available_fields:
            available_fields[source_name] = {}
        # Also add the short_name bucket so validator can accept both display and stored casing
        try:
            short_name = getattr(source, "short_name", None)
            if short_name and short_name not in available_fields:
                available_fields[short_name] = {}
        except Exception:
            pass

        try:
            # Get all entity definitions for this source
            entity_defs = await crud.entity_definition.get_multi_by_source_short_name(
                db, source_short_name=source.short_name
            )
            logger.debug(
                f"[{self.name}] Discovered {len(entity_defs)} entity definitions for {source_name}"
            )

            for entity_def in entity_defs:
                self._extract_entity_fields(
                    entity_def, source_name, available_fields, logger, schemas, resource_locator
                )

        except Exception as e:
            logger.debug(f"[{self.name}] Could not get entity fields for {source_name}: {e}")
            # Fallback: import module directly
            self._fallback_field_extraction(source, source_name, available_fields, logger)

    def _extract_entity_fields(
        self,
        entity_def: Any,
        source_name: str,
        available_fields: Dict,
        logger: Any,
        schemas: Any,
        resource_locator: Any,
    ) -> None:
        """Extract fields from an entity definition."""
        try:
            # Get the entity class
            try:
                entity_schema = schemas.EntityDefinition.model_validate(
                    entity_def, from_attributes=True
                )
                entity_class = resource_locator.get_entity_definition(entity_schema)
                logger.debug(f"[{self.name}] Entity class: {entity_class}")
            except Exception:
                # If conversion fails, fall back to direct resolution
                entity_class = resource_locator.get_entity_definition(entity_def)

            # Extract field names and descriptions
            self._extract_fields_from_class(entity_class, source_name, available_fields, logger)

        except Exception as e:
            logger.debug(f"[{self.name}] Failed to process entity definition: {e}")

    def _extract_fields_from_class(
        self, entity_class: Any, source_name: str, available_fields: Dict, logger: Any
    ) -> None:
        """Extract fields from a Pydantic model class."""
        try:
            # Pydantic v2
            if hasattr(entity_class, "model_fields"):
                for field_name, field_info in entity_class.model_fields.items():
                    if field_name.startswith("_"):
                        continue
                    description = self._get_field_description(field_info)
                    available_fields[source_name][field_name] = description or ""
            # Pydantic v1 fallback
            elif hasattr(entity_class, "__fields__"):
                for field_name, field in entity_class.__fields__.items():
                    if field_name.startswith("_"):
                        continue
                    description = getattr(getattr(field, "field_info", None), "description", None)
                    available_fields[source_name][field_name] = description or ""
        except Exception as e:
            logger.debug(f"[{self.name}] Failed to introspect fields for {entity_class}: {e}")

    def _get_field_description(self, field_info: Any) -> Optional[str]:
        """Get description from field info."""
        description = getattr(field_info, "description", None)
        # Some versions store extra in json_schema_extra
        if (
            not description
            and hasattr(field_info, "json_schema_extra")
            and isinstance(field_info.json_schema_extra, dict)
        ):
            description = field_info.json_schema_extra.get("description")
        return description

    def _fallback_field_extraction(
        self, source: Any, source_name: str, available_fields: Dict, logger: Any
    ) -> None:
        """Fallback method to extract fields by importing module directly."""
        try:
            import importlib
            import inspect

            from pydantic import BaseModel as PydanticBaseModel

            module = importlib.import_module(f"airweave.platform.entities.{source.short_name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, PydanticBaseModel) and hasattr(obj, "model_fields"):
                    for field_name, field_info in obj.model_fields.items():
                        if field_name.startswith("_"):
                            continue
                        description = self._get_field_description(field_info)
                        available_fields[source_name][field_name] = description or ""
        except Exception as e2:
            logger.debug(f"[{self.name}] Fallback introspection failed for {source_name}: {e2}")

    def _build_system_prompt(self, available_fields: Dict[str, Dict[str, str]]) -> str:
        """Build system prompt with available fields information.

        Args:
            available_fields: Dict of source names to available fields

        Returns:
            System prompt for the LLM
        """
        # Format available fields and sources for the prompt
        fields_description = "Available fields for filtering (with descriptions):\n\n"
        # Sources in the current collection (exact, case-sensitive)
        sources_in_collection = [s for s in available_fields.keys() if s != "common"]
        if sources_in_collection:
            fields_description += f"Sources in this collection: {sources_in_collection}\n\n"

        # Common fields (available for all entities)
        if "common" in available_fields and available_fields["common"]:
            fields_description += "Common fields (available for all entities):\n"
            for fname in sorted(available_fields["common"].keys()):
                desc = available_fields["common"].get(fname) or ""
                fields_description += f"  - {fname}: {desc}\n"
            fields_description += "\n"

        # Source-specific fields
        for source, fields in available_fields.items():
            if source != "common" and fields:
                fields_description += f"{source} fields:\n"
                for fname in sorted(fields.keys()):
                    desc = fields.get(fname) or ""
                    fields_description += f"  - {fname}: {desc}\n"

        return self._format_system_prompt(fields_description)

    def _format_system_prompt(self, fields_description: str) -> str:
        """Format the complete system prompt."""
        return (
            "You are a search query analyzer. Extract Qdrant filters from natural "
            "language queries.\n\n"
            "CRITICAL FIELD STRUCTURE INFORMATION:\n"
            "In the Qdrant database, fields are stored in a nested structure within the payload:\n"
            "- Fields marked with 'airweave_system_metadata.' prefix are nested under that object\n"
            "- Other fields are stored directly in the payload\n"
            "- The system will AUTOMATICALLY map the field names to their correct nested paths\n"
            "- You should use the field names AS SHOWN in the list below\n"
            "- DO NOT manually add 'airweave_system_metadata.' prefix - the system handles this\n\n"
            "For example:\n"
            "- If you see 'airweave_system_metadata.source_name' in the list, "
            "just use 'source_name' in your filter\n"
            "- If you see 'entity_id' in the list, use 'entity_id' as-is\n"
            "- The system knows which fields need the nested path and will apply "
            "it automatically\n\n"
            f"{fields_description}\n"
            "Generate Qdrant filter conditions in this format:\n"
            '- For exact matches: {"key": "field_name", "match": {"value": "exact_value"}}\n'
            '- For multiple values: {"key": "field_name", "match": {"any": ["value1", "value2"]}}\n'
            '- For date ranges: {"key": "field_name", "range": {"gte": "2024-01-01T00:00:00Z", '
            '"lte": "2024-12-31T23:59:59Z"}}\n'
            '- For number ranges: {"key": "field_name", "range": {"gte": 0, "lte": 100}}\n\n'
            "Common patterns to look for:\n"
            '- Source/platform mentions: "in Asana", "from GitHub", "on Slack" -> '
            "source_name field (will be mapped to airweave_system_metadata.source_name)\n"
            '- Status indicators: "open", "closed", "pending", "completed" -> '
            "status or state field\n"
            '- Time references: "last week", "yesterday", "past month" -> choose a '
            "date/time field that EXISTS for the relevant source (see lists above).\n"
            "  Examples of date fields you may see across sources include: created_time, "
            "last_edited_time, due_date, created_at, updated_at, published_at.\n"
            "  Prefer source-specific fields that appear in the field list for that source. "
            "Do not use a generic field name if it is not listed.\n"
            '- Assignee mentions: "assigned to John" -> assignee field\n'
            '- Priority levels: "high priority", "critical" -> priority field\n\n'
            "IMPORTANT CONSTRAINTS:\n"
            "- Do NOT invent sources or fields. Use only the sources listed above and only "
            "the field names explicitly listed for each source or in Common fields.\n"
            "- If you cannot confidently map a term to an available field, omit the filter "
            "and lower the confidence.\n"
            "- The value for source_name must match one of the listed sources exactly. "
            "If both a display-cased and lowercase variant are present (e.g., "
            "'PostgreSQL' and 'postgresql'), prefer the lowercase short_name "
            "(e.g., 'postgresql').\n"
            "- If you use a field that only exists for some sources, also include a matching "
            "source_name condition to scope the filter appropriately when the source is "
            "implied by the query.\n"
            "- When time-based language is used, identify the most relevant date field from "
            "the listed fields for the implicated source (e.g., for Notion use created_time "
            "or last_edited_time if listed). If ambiguous and no appropriate field is listed, "
            "omit the time filter and lower confidence.\n"
            "- When a user mentions a file name or document title, prefer fields whose "
            "description indicates they store the parent document/file title (e.g., "
            "md_parent_title) or the file name (e.g., name) over generic title fields.\n\n"
            "Be conservative with confidence:\n"
            "- High confidence (>0.8): Clear, unambiguous filter terms with exact field matches\n"
            "- Medium confidence (0.5-0.8): Likely filters but field names might vary\n"
            "- Low confidence (<0.5): Unclear or ambiguous, no matching fields\n\n"
            "The refined query should remove filter terms but keep the semantic search intent.\n\n"
            "IMPORTANT: Only suggest filters for fields that actually exist in the available "
            "fields list above. Remember that the system will automatically handle the nested "
            "path mapping for fields like source_name, sync_id, entity_type, etc."
        )

    def _map_to_qdrant_path(self, key: str) -> str:
        """Map field names to their actual Qdrant payload paths."""
        nested_fields = {
            "source_name",
            "entity_type",
            "sync_id",
            "sync_job_id",
            "airweave_created_at",
            "airweave_updated_at",
        }
        # Keep already-namespaced keys
        if isinstance(key, str) and key.startswith("airweave_system_metadata."):
            return key
        if key in nested_fields:
            return f"airweave_system_metadata.{key}"

        # Support PostgreSQL table.column notation by flattening to column name
        # since polymorphic entities store columns at the top level of payload.
        # Example: "source.labels" -> "labels"; also map id -> id_
        if isinstance(key, str) and "." in key:
            try:
                table, column = key.split(".", 1)
                if column == "id":
                    return "id_"
                return column
            except Exception:
                return key
        return key

    def _build_qdrant_filter(self, filter_conditions: List[Dict[str, Any]]) -> Optional[Dict]:
        if not filter_conditions:
            return None

        mapped_conditions: List[Dict[str, Any]] = []
        for cond in filter_conditions:
            original_key = cond.get("key")
            new_cond = cond.copy()
            new_cond["key"] = self._map_to_qdrant_path(original_key)
            mapped_conditions.append(new_cond)

            # If condition used table.column notation (not system metadata),
            # also scope by table_name to avoid cross-table collisions.
            if (
                isinstance(original_key, str)
                and "." in original_key
                and not original_key.startswith("airweave_system_metadata.")
            ):
                try:
                    table, _ = original_key.split(".", 1)
                    mapped_conditions.append({"key": "table_name", "match": {"value": table}})
                except Exception:
                    pass

        return {"must": mapped_conditions}
