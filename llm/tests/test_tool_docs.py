"""Auto-generated tool list rendering for SOUL.md."""
from llm.dispatch import _TOOL_CLASSES
from llm.tool_docs import _short_description, render_tools_markdown
from llm.tools import (
    LookupVendorsTool,
    MessageExternalPersonTool,
    ProposeTaskTool,
    RememberAboutEntityTool,
)
from llm.tools._common import ToolCategory


def test_render_groups_tools_by_category():
    md = render_tools_markdown()

    # Headers + ordering: Read → Immediate → Review.
    assert "**Read tools**" in md
    assert "**Immediate tools**" in md
    assert "**Write tools**" in md
    assert md.index("**Read tools**") < md.index("**Immediate tools**") < md.index("**Write tools**")


def test_render_includes_every_registered_tool_exactly_once():
    md = render_tools_markdown()
    for cls in _TOOL_CLASSES:
        name = cls().name
        bullet = f"`{name}`"
        assert md.count(bullet) == 1, f"expected one entry for {name}, found {md.count(bullet)}"


def test_review_tools_land_in_review_section():
    md = render_tools_markdown()
    review_block = md.split("**Write tools**", 1)[1]
    # propose_task and message_person are explicitly REVIEW.
    assert "`propose_task`" in review_block
    assert "`message_person`" in review_block


def test_read_only_tool_lands_in_read_section():
    md = render_tools_markdown()
    read_block = md.split("**Read tools**", 1)[1].split("**Immediate tools**", 1)[0]
    assert "`lookup_vendors`" in read_block
    assert "`lookup_properties`" in read_block


def test_default_read_write_lands_in_immediate_section():
    md = render_tools_markdown()
    immediate_block = md.split("**Immediate tools**", 1)[1].split("**Write tools**", 1)[0]
    assert "`remember_about_entity`" in immediate_block


def test_short_description_trims_to_first_sentence():
    tool = ProposeTaskTool()
    short = _short_description(tool)
    assert short.endswith(".")
    # Original description is multi-sentence; the trimmed form must be shorter.
    assert len(short) < len(tool.description)


def test_categories_match_explicit_overrides():
    assert ProposeTaskTool().category == ToolCategory.REVIEW
    assert MessageExternalPersonTool().category == ToolCategory.REVIEW
    assert LookupVendorsTool().category == ToolCategory.READ
    assert RememberAboutEntityTool().category == ToolCategory.IMMEDIATE
