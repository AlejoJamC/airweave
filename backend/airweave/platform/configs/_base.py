"""Base config class."""

from typing import Optional, get_args, get_origin

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticUndefined


def AuthRequiredField(*args, **kwargs):
    """Create a Field marked as required for OAuth URL templates.

    This helper marks config fields that must be provided BEFORE OAuth flow starts.
    These fields are used for template rendering in OAuth URLs (e.g., {instance_url}).

    Args:
        *args: Positional arguments passed to Field()
        **kwargs: Keyword arguments passed to Field()

    Returns:
        Field with required_for_auth=True in json_schema_extra

    Example:
        ```python
        class SalesforceConfig(BaseConfig):
            instance_url: str = AuthRequiredField(
                title="Instance URL",
                description="Your Salesforce instance (e.g., mycompany.salesforce.com)",
            )
        ```
    """
    if "json_schema_extra" not in kwargs:
        kwargs["json_schema_extra"] = {}
    kwargs["json_schema_extra"]["required_for_auth"] = True
    return Field(*args, **kwargs)


class BaseConfig(BaseModel):
    """Base config class with template config field support."""

    @classmethod
    def get_template_config_fields(cls) -> list[str]:
        """Get list of field names required for OAuth URL templates.

        Returns:
            List of field names marked with required_for_auth=True

        Example:
            ```python
            >>> SalesforceConfig.get_template_config_fields()
            ['instance_url']
            ```
        """
        template_fields = []
        for field_name, field_info in cls.model_fields.items():
            json_schema_extra = field_info.json_schema_extra or {}
            if json_schema_extra.get("required_for_auth"):
                template_fields.append(field_name)
        return template_fields

    @classmethod
    def extract_template_configs(cls, config_dict: dict) -> dict:
        """Extract only template config fields from config dict.

        Useful for OAuth URL template rendering.

        Args:
            config_dict: Full config dictionary

        Returns:
            Dictionary with only template config fields

        Example:
            ```python
            >>> config = {"instance_url": "mycompany.sf.com", "api_version": "v58.0"}
            >>> SalesforceConfig.extract_template_configs(config)
            {'instance_url': 'mycompany.sf.com'}
            ```
        """
        template_fields = cls.get_template_config_fields()
        return {k: v for k, v in config_dict.items() if k in template_fields}

    @classmethod
    def validate_template_configs(cls, config_dict: dict) -> None:
        """Validate that all template config fields are present.

        Should be called BEFORE starting OAuth flow to fail fast.

        Args:
            config_dict: Config dictionary to validate

        Raises:
            ValueError: If any template config fields are missing or None

        Example:
            ```python
            >>> SalesforceConfig.validate_template_configs({"api_version": "v58.0"})
            ValueError: Template config fields missing: instance_url.
            ```
        """
        template_fields = cls.get_template_config_fields()
        missing = [f for f in template_fields if f not in config_dict or config_dict[f] is None]

        if missing:
            raise ValueError(
                f"Template config fields missing: {', '.join(missing)}. "
                f"These must be provided before OAuth authentication."
            )


class ConfigField(BaseModel):
    """Config field model."""

    name: str
    title: str
    description: Optional[str] = None
    type: str
    required: bool = True  # Default to True for backward compatibility


_type_map = {str: "string", int: "number", float: "number", bool: "boolean"}


class Fields(BaseModel):
    """Fields model."""

    fields: list[ConfigField]

    @classmethod
    def from_config_class(cls, config_class: type[BaseConfig]) -> "Fields":
        """Create fields from config class."""
        fields = []
        for field_name, field_info in config_class.model_fields.items():
            # Get the actual type, handling Optional types
            annotation = field_info.annotation
            is_optional = False
            if get_origin(annotation) is Optional:
                annotation = get_args(annotation)[0]
                is_optional = True

            # Get the base type if it's a Field
            if hasattr(annotation, "__origin__"):
                annotation = annotation.__origin__

            # Map the type to string representation
            type_str = _type_map.get(annotation, "string")  # Default to string if type not found

            # Determine if field is required
            # A field is required if it has no default and is not Optional
            has_default = field_info.default is not PydanticUndefined
            is_required = not has_default and not is_optional

            fields.append(
                ConfigField(
                    name=field_name,
                    title=field_info.title or field_name,
                    description=field_info.description,
                    type=type_str,
                    required=is_required,
                )
            )
        return Fields(fields=fields)


class ConfigValues(BaseModel):
    """Config values model.

    Implements "flat dictionary" semantics, where no values are dictionaries.
    """

    # Allow arbitrary fields
    model_config = {
        "extra": "allow",
    }

    @model_validator(mode="after")
    def validate_config_values(self):
        """Validate that no values are dictionaries (depth 0)."""
        for key, value in self.__dict__.items():
            if isinstance(value, dict):
                raise ValueError(f"Value for '{key}' must not be a dictionary (depth 0 only)")
        return self
