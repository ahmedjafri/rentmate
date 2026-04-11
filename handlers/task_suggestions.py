"""Re-export from gql.services.task_suggestions for backward compatibility."""
from gql.services.task_suggestions import (  # noqa: F401
    CloseTaskSuggestionExecutor,
    CreateTaskSuggestionExecutor,
    MessagePersonSuggestionExecutor,
    ReplyInTaskSuggestionExecutor,
    SuggestionExecutor,
)
