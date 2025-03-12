"""Schemas for integration credentials."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from airweave.models.integration_credential import IntegrationType
from airweave.platform.auth.schemas import AuthType


class IntegrationCredentialBase(BaseModel):
    """Base class for integration credentials."""

    name: str
    integration_short_name: str
    description: Optional[str] = None
    integration_type: IntegrationType
    auth_type: AuthType
    auth_config_class: Optional[str] = None


class IntegrationCredentialCreate(IntegrationCredentialBase):
    """Create class for integration credentials."""

    encrypted_credentials: str


class IntegrationCredentialCreateEncrypted(IntegrationCredentialBase):
    """Create class for integration credentials with encrypted data."""

    encrypted_credentials: str


class IntegrationCredentialUpdate(BaseModel):
    """Update class for integration credentials."""

    name: Optional[str] = None
    description: Optional[str] = None
    encrypted_credentials: Optional[str] = None


class IntegrationCredentialInDBBase(IntegrationCredentialBase):
    """Base class for integration credentials in the database."""

    id: UUID
    organization_id: UUID
    encrypted_credentials: str

    class Config:
        """Pydantic configuration."""

        from_attributes = True


class IntegrationCredentialInDB(IntegrationCredentialInDBBase):
    """Integration credential in DB without decrypted data."""

    pass


class IntegrationCredential(IntegrationCredentialInDBBase):
    """Integration credential with decrypted data."""

    decrypted_credentials: dict
