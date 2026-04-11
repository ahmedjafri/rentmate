# ruff: noqa: E402
# Composite FKs all share org_id, causing harmless overlap warnings.
# org_id is always the same within an org, so the concurrent writes are safe.
import warnings

from sqlalchemy.exc import SAWarning

warnings.filterwarnings("ignore", message=r".*will copy column.*\.org_id.*", category=SAWarning)

from db.enums import (
    AgentSource,
    AutomationSource,
    ChannelType,
    SuggestionOption,
    SuggestionSource,
    SuggestionSourceEnum,
    SuggestionStatus,
    TaskCategory,
    TaskMode,
    TaskPriority,
    TaskSource,
    TaskStatus,
    Urgency,
)

from .account import User
from .agent_memory import AgentMemory
from .agent_trace import AgentTrace
from .memory_item import MemoryItem

# AutomationRevision removed — replaced by ScheduledTask
from .base import Base, EntityNote, HasCreatorId
from .documents import Document, DocumentTag
from .messaging import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    DraftApprovalStatus,
    Message,
    MessageReceipt,
    MessageType,
    ParticipantType,
)
from .rental import Lease, Property, Tenant, Unit
from .scheduled_task import ScheduledTask
from .settings import AppSetting
from .suggestions import Suggestion
from .tasks import Task, TaskNumberSequence

__all__ = [
    "User",
    "Base",
    "HasCreatorId",
    "EntityNote",
    "Property",
    "Unit",
    "Tenant",
    "Lease",
    "ParticipantType",
    "ConversationType",
    "MessageType",
    "DraftApprovalStatus",
    "Task",
    "TaskNumberSequence",
    "Suggestion",
    "Conversation",
    "ConversationParticipant",
    "Message",
    "MessageReceipt",
    "Document",
    "DocumentTag",
    "AgentMemory",
    "AgentTrace",
    "MemoryItem",
    "AppSetting",
    "ScheduledTask",
    "ChannelType",
    "TaskCategory",
    "TaskMode",
    "TaskPriority",
    "TaskSource",
    "TaskStatus",
    "Urgency",
    "AutomationSource",
    "AgentSource",
    "SuggestionSource",
    "SuggestionSourceEnum",
    "SuggestionStatus",
    "SuggestionOption",
]
