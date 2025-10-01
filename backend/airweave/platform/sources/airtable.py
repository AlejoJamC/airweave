"""Airtable source implementation for syncing bases, tables, records, and comments."""

from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from airweave.core.exceptions import TokenRefreshError
from airweave.platform.decorators import source
from airweave.platform.entities._base import Breadcrumb, ChunkEntity
from airweave.platform.entities.airtable import (
    AirtableAttachmentEntity,
    AirtableBaseEntity,
    AirtableCommentEntity,
    AirtableRecordEntity,
    AirtableTableEntity,
    AirtableUserEntity,
)
from airweave.platform.sources._base import BaseSource
from airweave.schemas.source_connection import AuthenticationMethod, OAuthType


@source(
    name="Airtable",
    short_name="airtable",
    auth_methods=[
        AuthenticationMethod.OAUTH_BROWSER,
        AuthenticationMethod.OAUTH_TOKEN,
        AuthenticationMethod.AUTH_PROVIDER,
    ],
    oauth_type=OAuthType.WITH_REFRESH,
    auth_config_class=None,
    config_class="AirtableConfig",
    labels=["Database", "Spreadsheet"],
    supports_continuous=False,
)
class AirtableSource(BaseSource):
    """Airtable source connector integrates with the Airtable API to extract and synchronize data.

    Connects to your Airtable bases and syncs bases, tables, records, comments, and attachments.
    """

    API_BASE = "https://api.airtable.com/v0"

    @classmethod
    async def create(
        cls, access_token: str, config: Optional[Dict[str, Any]] = None
    ) -> "AirtableSource":
        """Create a new Airtable source.

        Args:
            access_token: OAuth access token for Airtable API
            config: Optional configuration parameters

        Returns:
            Configured AirtableSource instance
        """
        instance = cls()
        instance.access_token = access_token

        # Store config values as instance attributes
        if config:
            instance.base_ids = config.get("base_ids", [])
            instance.table_ids = config.get("table_ids", [])
            instance.table_names = config.get("table_names", [])
            instance.view = config.get("view")
            instance.fields = config.get("fields", [])
            instance.filter_by_formula = config.get("filter_by_formula")
            instance.page_size = config.get("page_size", 100)
            instance.max_records = config.get("max_records")
            instance.include_attachments = config.get("include_attachments", True)
            instance.include_comments = config.get("include_comments", True)
            instance.attachment_max_bytes = config.get("attachment_max_bytes")
        else:
            instance.base_ids = []
            instance.table_ids = []
            instance.table_names = []
            instance.view = None
            instance.fields = []
            instance.filter_by_formula = None
            instance.page_size = 100
            instance.max_records = None
            instance.include_attachments = True
            instance.include_comments = True
            instance.attachment_max_bytes = None

        return instance

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True
    )
    async def _get_with_auth(
        self, client: httpx.AsyncClient, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict:
        """Make authenticated GET request to Airtable API with token manager support.

        This method uses the token manager for authentication and handles
        401 errors by refreshing the token and retrying. Also handles 429 rate limits.

        Rate limits:
        - 5 requests/second per base (enforced by Airtable)
        - 50 requests/second for all PAT traffic
        - 429 responses include Retry-After header (typically 30 seconds)

        Args:
            client: HTTP client to use for the request
            url: API endpoint URL
            params: Optional query parameters

        Returns:
            JSON response data

        Raises:
            httpx.HTTPStatusError: For 4xx/5xx responses after retries
            ValueError: If no access token is available
        """
        # Get a valid token (will refresh if needed)
        access_token = await self.get_access_token()
        if not access_token:
            raise ValueError("No access token available")

        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            response = await client.get(url, headers=headers, params=params, timeout=30.0)

            # Handle 401 Unauthorized - token might have expired
            if response.status_code == 401:
                self.logger.warning(f"Received 401 Unauthorized for {url}, refreshing token...")

                # If we have a token manager, try to refresh
                if self.token_manager:
                    try:
                        # Force refresh the token
                        new_token = await self.token_manager.refresh_on_unauthorized()
                        headers = {"Authorization": f"Bearer {new_token}"}

                        # Retry the request with the new token
                        self.logger.info(f"Retrying request with refreshed token: {url}")
                        response = await client.get(
                            url, headers=headers, params=params, timeout=30.0
                        )

                    except TokenRefreshError as e:
                        self.logger.error(f"Failed to refresh token: {str(e)}")
                        response.raise_for_status()
                else:
                    # No token manager, can't refresh
                    self.logger.error("No token manager available to refresh expired token")
                    response.raise_for_status()

            # Handle 429 Rate Limit
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "30")
                self.logger.warning(
                    f"Rate limit hit for {url}, waiting {retry_after} seconds before retry"
                )
                import asyncio

                await asyncio.sleep(float(retry_after))
                # Retry after waiting
                response = await client.get(url, headers=headers, params=params, timeout=30.0)

            # Raise for other HTTP errors
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, url)
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error accessing Airtable API: {url}, {str(e)}")
            raise

    def _handle_http_error(self, error: httpx.HTTPStatusError, url: str) -> None:
        """Handle HTTP errors with specific error messages.

        Critical errors (400, 401, 403, 500, 502) get detailed logging.
        """
        status = error.response.status_code
        error_messages = {
            400: "BAD_REQUEST - Invalid request format",
            401: "UNAUTHORIZED - Authentication failed",
            403: "FORBIDDEN - No permission for resource",
            500: "SERVER_ERROR - Airtable server issue",
            502: "SERVER_ERROR - Airtable server issue",
        }

        error_type = error_messages.get(status, f"HTTP_ERROR_{status}")
        self.logger.error(f"{error_type} ({status}) for {url}")

    async def _get_user_info(self, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """Get authenticated user information from whoami endpoint."""
        url = f"{self.API_BASE}/meta/whoami"
        try:
            return await self._get_with_auth(client, url)
        except Exception as e:
            self.logger.warning(f"Failed to get user info: {e}")
            return None

    async def _list_bases(self, client: httpx.AsyncClient) -> AsyncGenerator[Dict[str, Any], None]:
        """List all accessible bases via Meta API with pagination."""
        url = f"{self.API_BASE}/meta/bases"
        params: Dict[str, Any] = {}

        while True:
            try:
                data = await self._get_with_auth(client, url, params=params)
                bases = data.get("bases", [])

                for base in bases:
                    yield base

                # Check for pagination
                offset = data.get("offset")
                if not offset:
                    break
                params["offset"] = offset

            except Exception as e:
                self.logger.error(f"Error listing bases: {e}")
                break

    async def _get_base_schema(self, client: httpx.AsyncClient, base_id: str) -> Dict[str, Any]:
        """Get the schema for a specific base including tables and fields."""
        url = f"{self.API_BASE}/meta/bases/{base_id}/tables"
        return await self._get_with_auth(client, url)

    async def _list_records(
        self,
        client: httpx.AsyncClient,
        base_id: str,
        table_id_or_name: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """List all records in a table with pagination."""
        url = f"{self.API_BASE}/{base_id}/{table_id_or_name}"
        params: Dict[str, Any] = {}

        if self.page_size:
            params["pageSize"] = min(int(self.page_size), 100)
        if self.max_records:
            params["maxRecords"] = int(self.max_records)
        if self.view:
            params["view"] = self.view
        if self.filter_by_formula:
            params["filterByFormula"] = self.filter_by_formula
        if self.fields:
            params["fields"] = self.fields

        while True:
            try:
                data = await self._get_with_auth(client, url, params=params)
                records = data.get("records", [])

                for record in records:
                    yield record

                # Check for pagination
                offset = data.get("offset")
                if not offset:
                    break
                params["offset"] = offset

            except Exception as e:
                self.logger.error(f"Error listing records for {base_id}/{table_id_or_name}: {e}")
                break

    async def _list_comments(
        self, client: httpx.AsyncClient, base_id: str, table_id: str, record_id: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """List all comments for a record with pagination."""
        url = f"{self.API_BASE}/{base_id}/{table_id}/{record_id}/comments"
        params: Dict[str, Any] = {"pageSize": 100}

        while True:
            try:
                data = await self._get_with_auth(client, url, params=params)
                comments = data.get("comments", [])

                for comment in comments:
                    yield comment

                # Check for pagination
                offset = data.get("offset")
                if not offset:
                    break
                params["offset"] = offset

            except Exception as e:
                self.logger.warning(f"Error listing comments for {record_id}: {e}")
                break

    async def _generate_base_entities(
        self, client: httpx.AsyncClient
    ) -> AsyncGenerator[ChunkEntity, None]:
        """Generate base entities.

        Note: Bases must already exist in Airtable. The API does not provide
        endpoints to create bases programmatically (requires special permissions).
        We either:
        1. Use base IDs specified in config, or
        2. List all accessible bases via the Meta API

        This is a fundamental Airtable API limitation - all other connectors
        (tables, records) are created within existing bases.
        """
        # If specific base IDs are configured, use those
        if self.base_ids:
            for base_id in self.base_ids:
                try:
                    # We don't have a get single base endpoint, so create minimal entity
                    yield AirtableBaseEntity(
                        entity_id=base_id,
                        breadcrumbs=[],
                        base_id=base_id,
                        name=base_id,  # Will be updated when we get schema
                        url=f"https://airtable.com/{base_id}",
                    )
                except Exception as e:
                    self.logger.error(f"Error creating base entity for {base_id}: {e}")
        else:
            # List all accessible bases
            async for base in self._list_bases(client):
                try:
                    base_id = base.get("id")
                    if not base_id:
                        continue

                    yield AirtableBaseEntity(
                        entity_id=base_id,
                        breadcrumbs=[],
                        base_id=base_id,
                        name=base.get("name", base_id),
                        permission_level=base.get("permissionLevel"),
                        url=f"https://airtable.com/{base_id}",
                    )
                except Exception as e:
                    self.logger.error(f"Error creating base entity: {e}")

    async def _generate_table_entities(
        self, client: httpx.AsyncClient, base_id: str, base_name: str, base_breadcrumb: Breadcrumb
    ) -> AsyncGenerator[ChunkEntity, None]:
        """Generate table entities for a base."""
        try:
            schema = await self._get_base_schema(client, base_id)
            tables = schema.get("tables", [])

            for table in tables:
                table_id = table.get("id")
                table_name = table.get("name")

                if not table_id:
                    continue

                # Filter by table IDs or names if specified
                if self.table_ids and table_id not in self.table_ids:
                    continue
                if self.table_names and table_name not in self.table_names:
                    continue

                # Get primary field name if available
                primary_field_name = None
                fields = table.get("fields", [])
                for field in fields:
                    if field.get("type") == "primaryField" or field.get("isPrimary"):
                        primary_field_name = field.get("name")
                        break
                if not primary_field_name and fields:
                    # First field is usually the primary
                    primary_field_name = fields[0].get("name")

                yield AirtableTableEntity(
                    entity_id=table_id,
                    breadcrumbs=[base_breadcrumb],
                    table_id=table_id,
                    base_id=base_id,
                    name=table_name or table_id,
                    description=table.get("description"),
                    fields_schema=fields,
                    primary_field_name=primary_field_name,
                    view_count=len(table.get("views", [])),
                )

        except Exception as e:
            self.logger.error(f"Error generating table entities for base {base_id}: {e}")

    async def _generate_record_entities(
        self,
        client: httpx.AsyncClient,
        base_id: str,
        table_id: str,
        table_name: str,
        table_breadcrumbs: List[Breadcrumb],
    ) -> AsyncGenerator[ChunkEntity, None]:
        """Generate record entities for a table."""
        try:
            async for record in self._list_records(client, base_id, table_id):
                record_id = record.get("id")
                if not record_id:
                    continue

                yield AirtableRecordEntity(
                    entity_id=record_id,
                    breadcrumbs=table_breadcrumbs,
                    record_id=record_id,
                    base_id=base_id,
                    table_id=table_id,
                    table_name=table_name,
                    fields=record.get("fields", {}),
                    created_time=record.get("createdTime"),
                )

        except Exception as e:
            self.logger.error(f"Error generating record entities for {base_id}/{table_id}: {e}")

    async def _generate_comment_entities(
        self,
        client: httpx.AsyncClient,
        base_id: str,
        table_id: str,
        record: Dict[str, Any],
        record_breadcrumbs: List[Breadcrumb],
    ) -> AsyncGenerator[ChunkEntity, None]:
        """Generate comment entities for a record."""
        if not self.include_comments:
            return

        record_id = record.get("id")
        if not record_id:
            return

        try:
            async for comment in self._list_comments(client, base_id, table_id, record_id):
                comment_id = comment.get("id")
                if not comment_id:
                    continue

                author = comment.get("author", {})

                yield AirtableCommentEntity(
                    entity_id=comment_id,
                    breadcrumbs=record_breadcrumbs,
                    comment_id=comment_id,
                    record_id=record_id,
                    base_id=base_id,
                    table_id=table_id,
                    text=comment.get("text", ""),
                    author_id=author.get("id"),
                    author_email=author.get("email"),
                    author_name=author.get("name"),
                    created_time=comment.get("createdTime"),
                    last_updated_time=comment.get("lastUpdatedTime"),
                )

        except Exception as e:
            self.logger.warning(f"Error generating comment entities for record {record_id}: {e}")

    def _should_process_attachment(self, size: Optional[int], filename: str) -> bool:
        """Check if attachment should be processed based on size limit."""
        if self.attachment_max_bytes and size and size > self.attachment_max_bytes:
            self.logger.info(f"Skipping large attachment {filename} ({size} bytes)")
            return False
        return True

    def _create_attachment_entity(
        self,
        attachment: Dict[str, Any],
        base_id: str,
        table_id: str,
        table_name: str,
        record_id: str,
        field_name: str,
        record_breadcrumbs: List[Breadcrumb],
    ) -> Optional[AirtableAttachmentEntity]:
        """Create an attachment entity from attachment data."""
        url = attachment.get("url")
        if not url:
            return None

        att_id = attachment.get("id", f"{record_id}:{field_name}")
        filename = attachment.get("filename") or attachment.get("name") or "attachment"
        mime_type = attachment.get("type")
        size = attachment.get("size")

        if not self._should_process_attachment(size, filename):
            return None

        return AirtableAttachmentEntity(
            entity_id=att_id,
            breadcrumbs=record_breadcrumbs,
            file_id=att_id,
            name=filename,
            mime_type=mime_type,
            size=size,
            total_size=size,
            download_url=url,
            base_id=base_id,
            table_id=table_id,
            table_name=table_name,
            record_id=record_id,
            field_name=field_name,
        )

    async def _generate_attachment_entities(
        self,
        client: httpx.AsyncClient,
        base_id: str,
        table_id: str,
        table_name: str,
        record: Dict[str, Any],
        record_breadcrumbs: List[Breadcrumb],
    ) -> AsyncGenerator[ChunkEntity, None]:
        """Generate attachment entities for a record."""
        if not self.include_attachments:
            return

        record_id = record.get("id")
        if not record_id:
            return

        fields = record.get("fields", {})

        # Look for attachment fields
        for field_name, field_value in fields.items():
            if not isinstance(field_value, list):
                continue

            for attachment in field_value:
                if not isinstance(attachment, dict):
                    continue

                # Create attachment entity
                file_entity = self._create_attachment_entity(
                    attachment,
                    base_id,
                    table_id,
                    table_name,
                    record_id,
                    field_name,
                    record_breadcrumbs,
                )
                if not file_entity:
                    continue

                # Process the file entity (download and extract content)
                try:
                    # Airtable URLs expire after 2 hours, so download immediately
                    processed_entity = await self.process_file_entity(
                        file_entity=file_entity,
                        headers=None,  # Airtable attachment URLs are pre-signed
                    )

                    if processed_entity:
                        yield processed_entity
                except Exception as e:
                    self.logger.error(f"Error processing attachment {file_entity.file_id}: {e}")

    async def generate_entities(self) -> AsyncGenerator[ChunkEntity, None]:
        """Generate all entities from Airtable."""
        async with self.http_client() as client:
            # First, yield the user entity
            user_info = await self._get_user_info(client)
            if user_info:
                try:
                    yield AirtableUserEntity(
                        entity_id=user_info.get("id", "unknown"),
                        breadcrumbs=[],
                        user_id=user_info.get("id", "unknown"),
                        email=user_info.get("email"),
                        scopes=user_info.get("scopes"),
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to create user entity: {e}")

            # Generate base entities
            async for base_entity in self._generate_base_entities(client):
                yield base_entity

                base_id = base_entity.base_id
                base_name = base_entity.name

                base_breadcrumb = Breadcrumb(
                    entity_id=base_id,
                    name=base_name,
                    type="base",
                )

                # Generate table entities for this base
                async for table_entity in self._generate_table_entities(
                    client, base_id, base_name, base_breadcrumb
                ):
                    yield table_entity

                    table_id = table_entity.table_id
                    table_name = table_entity.name

                    table_breadcrumb = Breadcrumb(
                        entity_id=table_id,
                        name=table_name,
                        type="table",
                    )
                    table_breadcrumbs = [base_breadcrumb, table_breadcrumb]

                    # Generate record entities for this table
                    async for record in self._list_records(client, base_id, table_id):
                        record_id = record.get("id")
                        if not record_id:
                            continue

                        # Yield the record entity
                        record_entity = AirtableRecordEntity(
                            entity_id=record_id,
                            breadcrumbs=table_breadcrumbs,
                            record_id=record_id,
                            base_id=base_id,
                            table_id=table_id,
                            table_name=table_name,
                            fields=record.get("fields", {}),
                            created_time=record.get("createdTime"),
                        )
                        yield record_entity

                        record_breadcrumb = Breadcrumb(
                            entity_id=record_id,
                            name=record_id,
                            type="record",
                        )
                        record_breadcrumbs = [*table_breadcrumbs, record_breadcrumb]

                        # Generate comment entities for this record
                        async for comment_entity in self._generate_comment_entities(
                            client, base_id, table_id, record, record_breadcrumbs
                        ):
                            yield comment_entity

                        # Generate attachment entities for this record
                        async for attachment_entity in self._generate_attachment_entities(
                            client, base_id, table_id, table_name, record, record_breadcrumbs
                        ):
                            yield attachment_entity

    async def validate(self) -> bool:
        """Verify OAuth2 token by pinging Airtable's bases endpoint."""
        return await self._validate_oauth2(
            ping_url=f"{self.API_BASE}/meta/bases",
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
