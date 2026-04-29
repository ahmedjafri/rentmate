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
from .agent_run import AgentRun, AgentRunFlag, AgentRunReview
from .agent_trace import AgentTrace

# AutomationRevision removed — replaced by Routine
from .base import Base, EntityNote, HasCreatorId, IdSequence
from .documents import Document, DocumentTag
from .memory_item import MemoryItem
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
from .notifications import Notification
from .rental import Lease, LeaseTenant, Property, Tenant, Unit
from .routine import Routine
from .settings import AppSetting
from .suggestions import Suggestion
from .tasks import Task

__all__ = [
    "User",
    "Base",
    "HasCreatorId",
    "EntityNote",
    "Property",
    "Unit",
    "Tenant",
    "Lease",
    "LeaseTenant",
    "ParticipantType",
    "ConversationType",
    "MessageType",
    "DraftApprovalStatus",
    "Task",
    "IdSequence",
    "Suggestion",
    "Conversation",
    "ConversationParticipant",
    "Message",
    "MessageReceipt",
    "Notification",
    "Document",
    "DocumentTag",
    "AgentMemory",
    "AgentRun",
    "AgentRunFlag",
    "AgentRunReview",
    "AgentTrace",
    "MemoryItem",
    "AppSetting",
    "Routine",
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
