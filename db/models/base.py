from sqlalchemy import Column, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class HasContext:
    """Mixin for entities that support agent context notes."""
    context = Column(Text, nullable=True)
