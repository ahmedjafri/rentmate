"""RentMate agent tool classes.

Includes suggestion tools (propose_task, close_task, message_person).

When a tool creates a visible entity/action during a chat, it queues a chat
message via ``pending_suggestion_messages``.  The chat handler flushes these
*after* persisting the AI reply so they appear below the agent response in the
conversation timeline.  The conversation_id is communicated via the
``active_conversation_id`` context variable, set by the chat handler before
the agent runs.
"""
from llm.tools._common import (
    Tool,
    ToolCategory,
    ToolMode,
    active_conversation_id,
    current_request_context,
    current_user_message,
    pending_suggestion_messages,
    simulation_suggestions,
)
from llm.tools.documents import (
    AnalyzeDocumentTool,
    CreateDocumentTool,
    ReadDocumentTool,
)
from llm.tools.entities import (
    CreatePropertyTool,
    CreateTenantTool,
    LookupPropertiesTool,
    LookupTenantsTool,
)
from llm.tools.leases import (
    AddTenantToLeaseTool,
    CreateLeaseTool,
    LookupLeasesTool,
    RemoveTenantFromLeaseTool,
    TerminateLeaseTool,
    UpdateLeaseTool,
)
from llm.tools.memory import (
    AddTaskNoteTool,
    EditMemoryTool,
    RecallMemoryTool,
    RememberAboutEntityTool,
)
from llm.tools.messaging import MessageExternalPersonTool
from llm.tools.onboarding import UpdateOnboardingTool
from llm.tools.task_review import AskManagerTool, RecordTaskReviewTool
from llm.tools.tasks import (
    CloseTaskTool,
    CreateRoutineTool,
    CreateSuggestionTool,
    ListTasksTool,
    ProposeTaskTool,
    UpdateTaskProgressTool,
)
from llm.tools.time_tools import HasHappenedTool
from llm.tools.vendors import CreateVendorTool, LookupVendorsTool

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
]
