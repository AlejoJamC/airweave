"""Trello bongo implementation.

Creates, updates, and deletes test cards via the real Trello API using OAuth1.
"""

import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from monke.bongos.base_bongo import BaseBongo
from monke.utils.logging import get_logger


class TrelloBongo(BaseBongo):
    """Bongo for Trello that creates boards, lists, and cards for E2E testing.

    Uses OAuth1 for authentication and embeds verification tokens in card descriptions.
    """

    connector_type = "trello"
    API_BASE = "https://api.trello.com/1"

    def __init__(self, credentials: Dict[str, Any], **kwargs):
        """Initialize the Trello bongo.

        Args:
            credentials: Dict with oauth_token and oauth_token_secret
            **kwargs: Configuration from test config file
        """
        super().__init__(credentials)
        self.oauth_token: str = credentials["oauth_token"]
        self.oauth_token_secret: str = credentials["oauth_token_secret"]
        self.consumer_key: str = credentials.get("consumer_key", "")
        self.consumer_secret: str = credentials.get("consumer_secret", "")

        # Test configuration
        self.entity_count: int = int(kwargs.get("entity_count", 3))
        self.openai_model: str = kwargs.get("openai_model", "gpt-4.1-mini")
        self.max_concurrency: int = int(kwargs.get("max_concurrency", 3))

        # Runtime state - track created entities
        self._board_id: Optional[str] = None
        self._list_id: Optional[str] = None
        self._cards: List[Dict[str, Any]] = []
        self._checklists: List[Dict[str, Any]] = []

        # Rate limiting
        self.last_request_time = 0.0
        self.min_delay = 0.3  # 300ms between requests

        self.logger = get_logger("trello_bongo")

    def _percent_encode(self, value: str) -> str:
        """Percent-encode for OAuth1."""
        return quote(str(value), safe="~")

    def _build_oauth1_params(self) -> Dict[str, str]:
        """Build OAuth1 protocol parameters."""
        return {
            "oauth_consumer_key": self.consumer_key,
            "oauth_token": self.oauth_token,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_nonce": secrets.token_urlsafe(32),
            "oauth_version": "1.0",
        }

    def _sign_request(self, method: str, url: str, params: Dict[str, str]) -> str:
        """Sign OAuth1 request using HMAC-SHA1."""
        # Build signature base string
        sorted_params = sorted(params.items())
        param_str = "&".join(
            f"{self._percent_encode(k)}={self._percent_encode(v)}"
            for k, v in sorted_params
        )

        base_parts = [
            method.upper(),
            self._percent_encode(url),
            self._percent_encode(param_str),
        ]
        base_string = "&".join(base_parts)

        # Build signing key
        signing_key = (
            f"{self._percent_encode(self.consumer_secret)}&"
            f"{self._percent_encode(self.oauth_token_secret)}"
        )

        # Sign with HMAC-SHA1
        signature_bytes = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        ).digest()

        # Base64 encode
        import base64

        return base64.b64encode(signature_bytes).decode("utf-8")

    def _build_auth_header(self, oauth_params: Dict[str, str]) -> str:
        """Build OAuth1 Authorization header."""
        sorted_items = sorted(oauth_params.items())
        param_strings = [
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted_items
        ]
        return "OAuth " + ", ".join(param_strings)

    async def _rate_limit(self):
        """Simple rate limiting."""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_delay:
            await asyncio.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        query_params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """Make authenticated OAuth1 request to Trello API."""
        await self._rate_limit()

        # Build OAuth parameters
        oauth_params = self._build_oauth1_params()

        # Merge with query parameters for signing
        all_params = {**oauth_params}
        if query_params:
            all_params.update({k: str(v) for k, v in query_params.items()})

        # Sign the request
        signature = self._sign_request(method, url, all_params)
        oauth_params["oauth_signature"] = signature

        # Build Authorization header
        auth_header = self._build_auth_header(oauth_params)

        # Make request
        request_kwargs = {
            "headers": {"Authorization": auth_header},
            "params": query_params,
        }

        if json_data:
            request_kwargs["json"] = json_data

        response = await client.request(method, url, **request_kwargs)
        response.raise_for_status()

        # Trello returns JSON
        return response.json() if response.text else {}

    async def create_entities(self) -> List[Dict[str, Any]]:
        """Create comprehensive test entities.

        Creates:
        - 1 test board
        - 1 test list
        - N cards (based on entity_count)
        - 1 checklist per card

        Returns:
            List of entity descriptors for verification
        """
        self.logger.info(
            f"🥁 Creating {self.entity_count} Trello cards with checklists"
        )

        # Ensure test board and list exist
        await self._ensure_board()
        await self._ensure_list()

        from monke.generation.trello import (
            generate_trello_card,
            generate_trello_checklist,
        )

        all_entities: List[Dict[str, Any]] = []
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async with httpx.AsyncClient() as client:

            async def create_one_card() -> Dict[str, Any]:
                """Create a single card with checklist."""
                async with semaphore:
                    try:
                        # Generate unique token for this card
                        card_token = str(uuid.uuid4())[:8]

                        self.logger.info(f"Creating card with token {card_token}")

                        # Generate card content
                        title, description, labels = await generate_trello_card(
                            self.openai_model, card_token
                        )

                        # Create card
                        card_data = await self._request(
                            client,
                            "POST",
                            f"{self.API_BASE}/cards",
                            query_params={
                                "name": title,
                                "desc": description,
                                "idList": self._list_id,
                            },
                        )

                        card_descriptor = {
                            "type": "card",
                            "id": card_data["id"],
                            "name": title,
                            "token": card_token,
                            "expected_content": card_token,
                            "path": f"trello/card/{card_data['id']}",
                        }
                        self._cards.append(card_descriptor)
                        all_entities.append(card_descriptor)

                        # Create checklist for this card
                        checklist_token = str(uuid.uuid4())[:8]

                        self.logger.info(
                            f"  Creating checklist for card {card_data['id']} with token {checklist_token}"
                        )

                        checklist_data_generated = await generate_trello_checklist(
                            self.openai_model, checklist_token
                        )

                        # Create checklist
                        checklist = await self._request(
                            client,
                            "POST",
                            f"{self.API_BASE}/checklists",
                            query_params={
                                "idCard": card_data["id"],
                                "name": checklist_data_generated["name"],
                            },
                        )

                        # Add checklist items
                        for item in checklist_data_generated["items"]:
                            await self._request(
                                client,
                                "POST",
                                f"{self.API_BASE}/checklists/{checklist['id']}/checkItems",
                                query_params={"name": item["name"]},
                            )

                        checklist_descriptor = {
                            "type": "checklist",
                            "id": checklist["id"],
                            "parent_id": card_data["id"],
                            "name": checklist_data_generated["name"],
                            "token": checklist_token,
                            "expected_content": checklist_token,
                            "path": f"trello/checklist/{checklist['id']}",
                        }
                        self._checklists.append(checklist_descriptor)
                        all_entities.append(checklist_descriptor)

                        self.logger.info(
                            f"✅ Created card and checklist: {title[:50]}..."
                        )

                        return card_descriptor

                    except Exception as e:
                        self.logger.error(
                            f"❌ Error creating card: {type(e).__name__}: {str(e)}"
                        )
                        raise

            # Create all cards in parallel
            tasks = [create_one_card() for _ in range(self.entity_count)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check for errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Failed to create card {i + 1}: {result}")
                    raise result

        self.logger.info(
            f"✅ Created {len(self._cards)} cards and {len(self._checklists)} checklists"
        )

        self.created_entities = all_entities
        return all_entities

    async def update_entities(self) -> List[Dict[str, Any]]:
        """Update a subset of cards to test incremental sync.

        Returns:
            List of updated entity descriptors
        """
        self.logger.info("🥁 Updating Trello cards")

        if not self._cards:
            return []

        from monke.generation.trello import generate_trello_card

        updated_entities: List[Dict[str, Any]] = []
        count = min(2, len(self._cards))  # Update first 2 cards

        async with httpx.AsyncClient() as client:
            for i in range(count):
                card = self._cards[i]

                # Generate new content with SAME token
                title, description, _ = await generate_trello_card(
                    self.openai_model, card["token"]
                )

                # Update card
                await self._request(
                    client,
                    "PUT",
                    f"{self.API_BASE}/cards/{card['id']}",
                    query_params={
                        "name": title,
                        "desc": description,
                    },
                )

                updated_entities.append({**card, "name": title})

        return updated_entities

    async def delete_entities(self) -> List[str]:
        """Delete all created test entities.

        Returns:
            List of deleted entity IDs
        """
        self.logger.info("🥁 Deleting all Trello test entities")
        deleted_ids = await self.delete_specific_entities(self.created_entities)
        await self._delete_board()
        return deleted_ids

    async def delete_specific_entities(
        self, entities: List[Dict[str, Any]]
    ) -> List[str]:
        """Delete specific entities.

        Args:
            entities: List of entity descriptors to delete

        Returns:
            List of deleted entity IDs
        """
        self.logger.info(f"🥁 Deleting {len(entities)} Trello entities")
        deleted: List[str] = []

        async with httpx.AsyncClient() as client:
            # Delete checklists first
            for entity in entities:
                if entity["type"] == "checklist":
                    try:
                        await self._request(
                            client,
                            "DELETE",
                            f"{self.API_BASE}/checklists/{entity['id']}",
                        )
                        deleted.append(entity["id"])
                        self.logger.debug(f"Deleted checklist {entity['id']}")
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to delete checklist {entity['id']}: {e}"
                        )

            # Then delete cards
            for entity in entities:
                if entity["type"] == "card":
                    try:
                        await self._request(
                            client,
                            "DELETE",
                            f"{self.API_BASE}/cards/{entity['id']}",
                        )
                        deleted.append(entity["id"])
                        self.logger.debug(f"Deleted card {entity['id']}")
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to delete card {entity['id']}: {e}"
                        )

        return deleted

    async def cleanup(self):
        """Comprehensive cleanup of all test data."""
        self.logger.info("🧹 Starting comprehensive Trello cleanup")

        cleanup_stats = {
            "cards_deleted": 0,
            "checklists_deleted": 0,
            "boards_deleted": 0,
            "errors": 0,
        }

        try:
            # Clean up current session
            if self._checklists:
                for checklist in self._checklists:
                    try:
                        await self._delete_checklist(checklist["id"])
                        cleanup_stats["checklists_deleted"] += 1
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to delete checklist {checklist['id']}: {e}"
                        )
                        cleanup_stats["errors"] += 1

            if self._cards:
                for card in self._cards:
                    try:
                        await self._delete_card(card["id"])
                        cleanup_stats["cards_deleted"] += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to delete card {card['id']}: {e}")
                        cleanup_stats["errors"] += 1

            if self._board_id:
                await self._delete_board()
                cleanup_stats["boards_deleted"] += 1

            # Find and clean up orphaned test boards
            orphaned_boards = await self._find_test_boards()
            for board in orphaned_boards:
                try:
                    await self._delete_board_by_id(board["id"])
                    cleanup_stats["boards_deleted"] += 1
                except Exception:
                    cleanup_stats["errors"] += 1

            self.logger.info(
                f"🧹 Cleanup completed: {cleanup_stats['cards_deleted']} cards, "
                f"{cleanup_stats['checklists_deleted']} checklists, "
                f"{cleanup_stats['boards_deleted']} boards deleted, "
                f"{cleanup_stats['errors']} errors"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")
            # Don't re-raise - cleanup is best-effort

    async def _ensure_board(self):
        """Ensure test board exists."""
        if self._board_id:
            return

        board_name = f"monke-trello-test-{str(uuid.uuid4())[:6]}"

        async with httpx.AsyncClient() as client:
            board = await self._request(
                client,
                "POST",
                f"{self.API_BASE}/boards",
                query_params={
                    "name": board_name,
                    "defaultLists": "false",  # We'll create our own list
                },
            )

            self._board_id = board["id"]
            self.logger.info(f"Created test board: {board_name} ({self._board_id})")

    async def _ensure_list(self):
        """Ensure test list exists on the board."""
        if self._list_id:
            return

        list_name = "Test Cards"

        async with httpx.AsyncClient() as client:
            list_data = await self._request(
                client,
                "POST",
                f"{self.API_BASE}/lists",
                query_params={
                    "name": list_name,
                    "idBoard": self._board_id,
                },
            )

            self._list_id = list_data["id"]
            self.logger.info(f"Created test list: {list_name} ({self._list_id})")

    async def _delete_card(self, card_id: str):
        """Delete a card by ID."""
        async with httpx.AsyncClient() as client:
            await self._request(client, "DELETE", f"{self.API_BASE}/cards/{card_id}")
            self.logger.debug(f"Deleted card {card_id}")

    async def _delete_checklist(self, checklist_id: str):
        """Delete a checklist by ID."""
        async with httpx.AsyncClient() as client:
            await self._request(
                client, "DELETE", f"{self.API_BASE}/checklists/{checklist_id}"
            )
            self.logger.debug(f"Deleted checklist {checklist_id}")

    async def _delete_board(self):
        """Delete the test board."""
        if not self._board_id:
            return
        await self._delete_board_by_id(self._board_id)
        self._board_id = None

    async def _delete_board_by_id(self, board_id: str):
        """Delete a board by ID."""
        async with httpx.AsyncClient() as client:
            await self._request(client, "DELETE", f"{self.API_BASE}/boards/{board_id}")
            self.logger.info(f"Deleted board {board_id}")

    async def _find_test_boards(self) -> List[Dict[str, Any]]:
        """Find orphaned test boards."""
        test_boards = []

        try:
            async with httpx.AsyncClient() as client:
                boards = await self._request(
                    client,
                    "GET",
                    f"{self.API_BASE}/members/me/boards",
                    query_params={"fields": "id,name"},
                )

                for board in boards:
                    name = board.get("name", "")
                    if name.startswith("monke-trello-test-") or "monke" in name.lower():
                        test_boards.append(board)

        except Exception as e:
            self.logger.warning(f"Failed to find test boards: {e}")

        return test_boards
