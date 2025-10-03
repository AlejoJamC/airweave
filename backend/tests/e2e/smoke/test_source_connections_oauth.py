"""
Async test module for OAuth source connections.

Tests OAuth authentication flows including:
- OAuth browser flow
- OAuth token injection
- OAuth BYOC (Bring Your Own Credentials)
"""

import pytest
import httpx
import asyncio
from typing import Dict
from urllib.parse import parse_qs, urlparse, unquote


def verify_redirect_uri(provider_url: str, expected_api_url: str) -> None:
    """Helper to verify redirect_uri parameter in OAuth URL.

    Note: OAuth1 flows don't include redirect_uri in the authorization URL,
    so this check is skipped for OAuth1 providers (identified by oauth_token parameter).

    Args:
        provider_url: The OAuth provider URL
        expected_api_url: Expected API URL (e.g., http://localhost:8001)
    """
    parsed = urlparse(provider_url)
    params = parse_qs(parsed.query)

    # OAuth1 flows use oauth_token instead of redirect_uri in the authorization URL
    if "oauth_token" in params:
        # OAuth1 flow - redirect is configured during request token phase, not in auth URL
        return

    # OAuth2 flow - verify redirect_uri parameter
    assert "redirect_uri" in params, f"OAuth URL should have redirect_uri parameter: {provider_url}"
    redirect_uri = unquote(params["redirect_uri"][0])

    expected_redirect = f"{expected_api_url}/source-connections/callback"
    assert (
        redirect_uri == expected_redirect
    ), f"redirect_uri should be {expected_redirect}, got {redirect_uri}"


class TestOAuthAuthentication:
    """Test suite for OAuth authentication source connections."""

    @pytest.mark.asyncio
    async def test_oauth_browser_flow(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test OAuth browser flow (creates shell connection)."""
        payload = {
            "name": "Test Linear OAuth Browser",
            "short_name": "linear",
            "readable_collection_id": collection["readable_id"],
            "description": "Testing OAuth browser flow",
            "authentication": {},  # Empty for browser flow
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)

        response.raise_for_status()
        connection = response.json()

        # Verify OAuth browser flow response
        assert connection["id"]
        assert connection["auth"]["method"] == "oauth_browser"
        assert connection["auth"]["authenticated"] == False
        assert connection["status"] == "pending_auth"
        assert "auth_url" in connection["auth"]
        assert connection["auth"]["auth_url"] is not None

        # Follow the proxy URL to verify it redirects to OAuth provider
        auth_url = connection["auth"]["auth_url"]
        proxy_response = await api_client.get(auth_url, follow_redirects=False)
        assert proxy_response.status_code == 303, "Should redirect to OAuth provider"
        provider_url = proxy_response.headers.get("location")
        assert provider_url is not None, "Should have provider redirect URL"
        # Verify it's a valid OAuth URL (contains common OAuth patterns)
        assert any(
            pattern in provider_url for pattern in ["oauth", "authorize", "auth"]
        ), f"Should be OAuth URL: {provider_url}"

        # Verify redirect_uri parameter is correct for this environment
        verify_redirect_uri(provider_url, config.api_url)

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth_browser_defaults_sync_immediately_false(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test that OAuth browser defaults to sync_immediately=False when not specified."""
        payload = {
            "name": "Test OAuth Default No Sync",
            "short_name": "linear",
            "readable_collection_id": collection["readable_id"],
            "authentication": {},  # OAuth browser flow
            # Note: sync_immediately is not specified, should default to False
        }

        response = await api_client.post("/source-connections", json=payload)
        response.raise_for_status()
        connection = response.json()

        # OAuth browser should not have sync details (no immediate sync)
        assert connection["sync"] is None
        assert connection["status"] == "pending_auth"

        # Verify OAuth URL is correctly formed
        auth_url = connection["auth"]["auth_url"]
        proxy_response = await api_client.get(auth_url, follow_redirects=False)
        assert proxy_response.status_code == 303
        provider_url = proxy_response.headers.get("location")
        verify_redirect_uri(provider_url, config.api_url)

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth_token_injection_notion(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test OAuth token injection with Notion."""
        payload = {
            "name": "Test Notion Token Injection",
            "short_name": "notion",
            "readable_collection_id": collection["readable_id"],
            "description": "Testing OAuth token injection",
            "authentication": {"access_token": config.TEST_NOTION_TOKEN},
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)

        response.raise_for_status()
        connection = response.json()

        # Verify OAuth token injection response
        assert connection["id"]
        assert connection["auth"]["method"] == "oauth_token"
        assert connection["auth"]["authenticated"] == True
        assert connection["status"] in ["active", "syncing"]

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth_token_defaults_sync_immediately_true(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test that OAuth token injection defaults to sync_immediately=True when not specified."""
        payload = {
            "name": "Test OAuth Token Default Sync",
            "short_name": "notion",
            "readable_collection_id": collection["readable_id"],
            "authentication": {
                "access_token": config.TEST_NOTION_TOKEN,
            },
            # Note: sync_immediately is not specified, should default to True
        }

        response = await api_client.post("/source-connections", json=payload)
        response.raise_for_status()
        connection = response.json()

        # OAuth token should have sync details (immediate sync triggered)
        # Since sync_immediately defaults to True for token injection
        assert connection["auth"]["method"] == "oauth_token"
        assert connection["auth"]["authenticated"] == True
        assert connection["status"] in ["active", "syncing"]

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth_byoc_google_drive(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test OAuth BYOC with Google Drive."""
        client_id = config.TEST_GOOGLE_CLIENT_ID
        client_secret = config.TEST_GOOGLE_CLIENT_SECRET

        payload = {
            "name": "Test Google Drive BYOC",
            "short_name": "google_drive",
            "readable_collection_id": collection["readable_id"],
            "description": "Testing OAuth BYOC flow",
            "authentication": {"client_id": client_id, "client_secret": client_secret},
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)

        response.raise_for_status()
        connection = response.json()

        # BYOC returns oauth_browser after creation
        assert connection["id"]
        assert connection["auth"]["method"] == "oauth_browser"
        assert connection["auth"]["authenticated"] == False
        assert connection["status"] == "pending_auth"
        assert "auth_url" in connection["auth"]
        assert connection["auth"]["auth_url"] is not None

        # Verify BYOC generates valid OAuth redirect with custom client
        auth_url = connection["auth"]["auth_url"]
        proxy_response = await api_client.get(auth_url, follow_redirects=False)
        assert proxy_response.status_code == 303
        provider_url = proxy_response.headers.get("location")
        assert provider_url is not None
        # BYOC should use the custom client_id in the OAuth URL
        assert "client_id=" in provider_url, "OAuth URL should include client_id parameter"

        # Verify redirect_uri is correct
        verify_redirect_uri(provider_url, config.api_url)

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_minimal_oauth_payload(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test minimal OAuth payload defaults."""
        # Minimal payload - should default to OAuth browser
        payload = {"short_name": "notion", "readable_collection_id": collection["readable_id"]}

        response = await api_client.post("/source-connections", json=payload)

        response.raise_for_status()
        connection = response.json()

        # Verify defaults
        assert connection["name"] == "Notion Connection"  # Default name
        assert connection["status"] == "pending_auth"
        assert connection["auth"]["method"] == "oauth_browser"
        assert "auth_url" in connection["auth"]
        auth_url = connection["auth"]["auth_url"]
        assert isinstance(auth_url, str)
        assert auth_url.startswith("http://") or auth_url.startswith("https://")

        # Verify minimal payload generates valid OAuth redirect
        proxy_response = await api_client.get(auth_url, follow_redirects=False)
        assert proxy_response.status_code == 303
        provider_url = proxy_response.headers.get("location")
        assert provider_url is not None
        assert (
            "notion" in provider_url.lower() or "oauth" in provider_url.lower()
        ), f"Should be Notion OAuth URL: {provider_url}"

        # Verify redirect_uri is correct
        verify_redirect_uri(provider_url, config.api_url)

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth_wrong_auth_method(self, api_client: httpx.AsyncClient, collection: Dict):
        """Test using OAuth on source that doesn't support it."""
        # Try OAuth token on Stripe (which only supports API key)
        payload = {
            "name": "Wrong Auth Method",
            "short_name": "stripe",
            "readable_collection_id": collection["readable_id"],
            "authentication": {"access_token": "some_token"},
        }

        response = await api_client.post("/source-connections", json=payload)

        assert response.status_code == 400
        error = response.json()
        detail = error.get("detail", "").lower()
        assert "does not support" in detail or "unsupported" in detail

    @pytest.mark.asyncio
    async def test_oauth_browser_flow_with_sync_immediately(
        self, api_client: httpx.AsyncClient, collection: Dict
    ):
        """Test OAuth browser flow with sync_immediately=true."""
        payload = {
            "name": "OAuth Browser Flow with Sync Immediately",
            "short_name": "gmail",
            "readable_collection_id": collection["readable_id"],
            "authentication": {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
            },
            "sync_immediately": True,
        }
        response = await api_client.post("/source-connections", json=payload)
        assert response.status_code == 400
        error = response.json()
        assert "sync_immediately" in error["detail"].lower()

    @pytest.mark.asyncio
    async def test_oauth_byoc_with_sync_immediately(
        self, api_client: httpx.AsyncClient, collection: Dict
    ):
        """Test OAuth BYOC with sync_immediately=true."""
        payload = {
            "name": "OAuth BYOC with Sync Immediately",
            "short_name": "gmail",
            "readable_collection_id": collection["readable_id"],
            "authentication": {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
            },
            "sync_immediately": True,
        }
        response = await api_client.post("/source-connections", json=payload)
        assert response.status_code == 400
        error = response.json()
        assert "sync_immediately" in error["detail"].lower()

    @pytest.mark.asyncio
    async def test_oauth_byoc_requires_credentials(
        self, api_client: httpx.AsyncClient, collection: Dict
    ):
        """Test that sources requiring BYOC fail with empty authentication.

        Zendesk has requires_byoc=True, so it should fail without client credentials.
        """
        # Test with empty authentication dict - should fail
        payload = {
            "name": "Test Zendesk BYOC without Credentials",
            "short_name": "zendesk",
            "readable_collection_id": collection["readable_id"],
            "config": {
                "subdomain": "mycompany",
            },
            "authentication": {},  # Empty - should fail for BYOC sources
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)
        assert response.status_code == 400
        error = response.json()
        detail = error.get("detail", "").lower()
        # Should mention client credentials are required
        assert "client" in detail or "credentials" in detail or "byoc" in detail


class TestOAuth1Authentication:
    """Test suite for OAuth1 authentication source connections (e.g. Trello)."""

    @pytest.mark.asyncio
    async def test_oauth1_browser_flow(
        self, api_client: httpx.AsyncClient, collection: Dict, config
    ):
        """Test OAuth1 browser flow with Trello."""
        payload = {
            "name": "Test Trello OAuth1 Browser",
            "short_name": "trello",
            "readable_collection_id": collection["readable_id"],
            "description": "Testing OAuth1 browser flow",
            "authentication": {},  # Empty for browser flow
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)

        response.raise_for_status()
        connection = response.json()

        # Verify OAuth1 browser flow response
        assert connection["id"]
        assert connection["auth"]["method"] == "oauth_browser"
        assert connection["auth"]["authenticated"] == False
        assert connection["status"] == "pending_auth"
        assert "auth_url" in connection["auth"]
        assert connection["auth"]["auth_url"] is not None

        # Follow the proxy URL to verify it redirects to OAuth provider
        auth_url = connection["auth"]["auth_url"]
        proxy_response = await api_client.get(auth_url, follow_redirects=False)
        assert proxy_response.status_code == 303, "Should redirect to OAuth provider"
        provider_url = proxy_response.headers.get("location")
        assert provider_url is not None, "Should have provider redirect URL"
        # Verify it's a valid OAuth1 URL for Trello
        assert "trello.com" in provider_url.lower(), f"Should be Trello URL: {provider_url}"
        assert "oauth_token" in provider_url, "OAuth1 URL should contain oauth_token parameter"

        # Verify redirect_uri parameter is correct for this environment
        verify_redirect_uri(provider_url, config.api_url)

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth1_byoc_flow(self, api_client: httpx.AsyncClient, collection: Dict, config):
        """Test OAuth1 BYOC (Bring Your Own Credentials) with custom consumer key/secret."""
        payload = {
            "name": "Test Trello OAuth1 BYOC",
            "short_name": "trello",
            "readable_collection_id": collection["readable_id"],
            "description": "Testing OAuth1 BYOC flow",
            "authentication": {
                "consumer_key": config.TEST_TRELLO_CONSUMER_KEY,
                "consumer_secret": config.TEST_TRELLO_CONSUMER_SECRET,
            },
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)

        response.raise_for_status()
        connection = response.json()

        # BYOC returns oauth_browser after creation (shell connection)
        assert connection["id"]
        assert connection["auth"]["method"] == "oauth_browser"
        assert connection["auth"]["authenticated"] == False
        assert connection["status"] == "pending_auth"
        assert "auth_url" in connection["auth"]
        assert connection["auth"]["auth_url"] is not None

        # Verify BYOC generates valid OAuth1 redirect
        auth_url = connection["auth"]["auth_url"]
        proxy_response = await api_client.get(auth_url, follow_redirects=False)
        assert proxy_response.status_code == 303
        provider_url = proxy_response.headers.get("location")
        assert provider_url is not None
        # OAuth1 BYOC should have oauth_token in the authorization URL
        assert "oauth_token" in provider_url, "OAuth1 URL should include oauth_token parameter"

        # Verify redirect_uri is correct
        verify_redirect_uri(provider_url, config.api_url)

        # Cleanup
        await api_client.delete(f"/source-connections/{connection['id']}")

    @pytest.mark.asyncio
    async def test_oauth1_byoc_partial_credentials_fails(
        self, api_client: httpx.AsyncClient, collection: Dict
    ):
        """Test OAuth1 BYOC with only one credential (should fail validation)."""
        # Test with only consumer_key (missing consumer_secret)
        payload = {
            "name": "Test Trello OAuth1 BYOC Partial",
            "short_name": "trello",
            "readable_collection_id": collection["readable_id"],
            "authentication": {
                "consumer_key": "test_consumer_key_12345",
                # Missing consumer_secret
            },
            "sync_immediately": False,
        }

        response = await api_client.post("/source-connections", json=payload)
        assert response.status_code == 400
        error = response.json()
        detail = error.get("detail", "").lower()
        # Should mention both credentials are required
        assert "consumer" in detail or "both" in detail
