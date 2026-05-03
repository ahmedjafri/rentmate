"""RentMate agent tool classes.

Includes suggestion tools (propose_task, close_task, message_person).

When a tool creates a visible entity/action during a chat, it queues a chat
message via ``pending_suggestion_messages``.  The chat handler flushes these
*after* persisting the AI reply so they appear below the agent response in the
conversation timeline.  The conversation_id is communicated via the
``active_conversation_id`` context variable, set by the chat handler before
the agent runs.
"""
from agent.tools._common import (
    Tool,
    ToolCategory,
    ToolMode,
    active_conversation_id,
    current_request_context,
    current_user_message,
    pending_suggestion_messages,
    simulation_suggestions,
)
from agent.tools.documents import (
    AnalyzeDocumentTool,
    CreateDocumentTool,
    ReadDocumentTool,
)
from agent.tools.entities import (
    CreatePropertyTool,
    CreateTenantTool,
    LookupPropertiesTool,
    LookupTenantsTool,
)
from agent.tools.leases import (
    AddTenantToLeaseTool,
    CreateLeaseTool,
    LookupLeasesTool,
    RemoveTenantFromLeaseTool,
    TerminateLeaseTool,
    UpdateLeaseTool,
)
from agent.tools.memory import (
    AddTaskNoteTool,
    EditMemoryTool,
    RecallMemoryTool,
    RememberAboutEntityTool,
)
from agent.tools.messaging import MessageExternalPersonTool
from agent.tools.onboarding import UpdateOnboardingTool
from agent.tools.task_review import AskManagerTool, RecordTaskReviewTool
from agent.tools.tasks import (
    CloseTaskTool,
    CreateRoutineTool,
    CreateSuggestionTool,
    ListTasksTool,
    ProposeTaskTool,
    UpdateTaskProgressTool,
)
from agent.tools.time_tools import HasHappenedTool
from agent.tools.vendors import CreateVendorTool, LookupVendorsTool
from agent.tools.web_search import WebSearchTool

__all__ = [
    "Tool",
    "ToolCategory",
    "ToolMode",
    "active_conversation_id",
    "current_request_context",
    "current_user_message",
    "pending_suggestion_messages",
    "simulation_suggestions",
    "AddTaskNoteTool",
    "AddTenantToLeaseTool",
    "AnalyzeDocumentTool",
    "AskManagerTool",
    "CloseTaskTool",
    "CreateDocumentTool",
    "CreateLeaseTool",
    "CreatePropertyTool",
    "CreateRoutineTool",
    "CreateSuggestionTool",
    "CreateTenantTool",
    "CreateVendorTool",
    "EditMemoryTool",
    "HasHappenedTool",
    "ListTasksTool",
    "LookupLeasesTool",
    "LookupPropertiesTool",
    "LookupTenantsTool",
    "LookupVendorsTool",
    "MessageExternalPersonTool",
    "ProposeTaskTool",
    "ReadDocumentTool",
    "RecallMemoryTool",
    "RecordTaskReviewTool",
    "RememberAboutEntityTool",
    "RemoveTenantFromLeaseTool",
    "TerminateLeaseTool",
    "UpdateLeaseTool",
    "UpdateTaskProgressTool",
    "UpdateOnboardingTool",
    "WebSearchTool",
]
