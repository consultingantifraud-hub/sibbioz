"""Metadata helpers for amoCRM."""

from .cache import MetadataBundle, load_metadata
from .mapping import configure_mapping, flatten_custom_fields, map_loss_reason, map_status, map_user

__all__ = [
    "MetadataBundle",
    "configure_mapping",
    "load_metadata",
    "flatten_custom_fields",
    "map_loss_reason",
    "map_status",
    "map_user",
]
