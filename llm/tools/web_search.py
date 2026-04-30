import json
import os
from typing import Any

from tavily import TavilyClient

from llm.tools._common import Tool, ToolMode


class WebSearchTool(Tool):
    mode = ToolMode.READ_ONLY

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for information. Use this tool when you need to:\n"
            "- Look up Washington State landlord-tenant law or compliance requirements "
            "(search the RCW at https://app.leg.wa.gov/rcw/, e.g. RCW 59.18 for residential "
            "landlord-tenant act, RCW 59.20 for manufactured/mobile home landlord-tenant, etc.)\n"
            "- Find local vendors or contractors in the tenant's area (plumbers, electricians, "
            "HVAC, roofers, locksmiths, etc.) when the internal vendor list has no suitable match\n"
            "- Verify current permit requirements, inspection fees, or local ordinances\n"
            "Prefer targeted queries — include the city/county for vendor searches and the "
            "relevant RCW chapter number for legal lookups."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. For WA law use 'RCW <chapter> <topic>', e.g. "
                        "'RCW 59.18 notice to cure'. For vendors include city, e.g. "
                        "'licensed plumber Seattle WA'."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1–10, default 5).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        query: str = kwargs["query"]
        max_results: int = min(max(int(kwargs.get("max_results", 5)), 1), 10)
        api_key = os.environ.get("TAVILY_KEY") or os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return json.dumps({"error": "TAVILY_KEY not configured"})
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)
        results = [
            {"title": r.get("title"), "url": r.get("url"), "content": r.get("content")}
            for r in response.get("results", [])
        ]
        return json.dumps({"query": query, "results": results})
