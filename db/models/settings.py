"""App settings stored in the database as key-value pairs."""
import uuid

from sqlalchemy import Column, DateTime, String, Text

from .base import Base


class AppSetting(Base):
    """Key-value store for application settings.

    Replaces data/settings.json and data/integrations.json.
    Each row stores a settings namespace (e.g. "autonomy", "integrations.quo")
    with its JSON value.
    """
    __tablename__ = "app_settings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)  # JSON string
    updated_at = Column(DateTime, nullable=True)
