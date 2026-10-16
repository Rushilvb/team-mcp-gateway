"""Tool naming: every upstream tool is exposed as <server_slug>__<tool_name>.

Slugs may only contain [a-z0-9-] (no underscore), so splitting on the FIRST
"__" is always unambiguous, even if the upstream's own tool name contains "__".
Two servers that both have `get_info` become `woo__get_info` and
`notes__get_info`: collisions are impossible, and the model can see which
server each tool belongs to.
"""

import re

SEPARATOR = "__"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def prefixed(slug: str, tool: str) -> str:
    return f"{slug}{SEPARATOR}{tool}"


def split(name: str) -> tuple[str, str] | None:
    """'notes__add_note' -> ('notes', 'add_note'); None if not a valid prefixed name."""
    slug, sep, tool = name.partition(SEPARATOR)
    if not sep or not tool or not valid_slug(slug):
        return None
    return slug, tool
