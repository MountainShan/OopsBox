# dashboard/classification.py
"""Project classification and agent access gates.

Classification decides which agents may act on a project. Gate hardness
follows who bears the risk (spec §9.3):

  work-restricted -> claude/codex : blocked  (company compliance)
  personal        -> kiro         : hidden   (user's own privacy)
"""

VALID_CLASSIFICATIONS: tuple[str, ...] = ("personal", "work", "work-restricted")

DEFAULT_CLASSIFICATION: str = "personal"

# state is one of:
#   "available" — shown and usable
#   "hidden"    — not offered by default; the user may open it deliberately
#   "blocked"   — no entry point exists at all
_AGENT_ACCESS: dict[str, dict[str, str]] = {
    "personal": {"claude": "available", "codex": "available", "kiro": "hidden"},
    "work": {"claude": "available", "codex": "available", "kiro": "available"},
    "work-restricted": {"claude": "blocked", "codex": "blocked", "kiro": "available"},
}


# Posture for an unknown or corrupt classification: per agent, the strictest
# state that appears in any table (blocked > hidden > available). Borrowing a
# single table is not enough - work-restricted leaves kiro "available", which
# would open that gate precisely when the registry is broken.
_FALLBACK_ACCESS: dict[str, str] = {
    "claude": "blocked",
    "codex": "blocked",
    "kiro": "hidden",
}


# Agents the table does not name (the experimental ones): open where the
# cloud agents are, closed on work-restricted, closed when the value is
# unknown. Where their data goes has not been looked at, so a compliance
# project does not get them by default.
_OTHER_AGENTS: dict[str, str] = {"personal": "available", "work": "available"}


def other_agent_access(classification: str) -> str:
    return _OTHER_AGENTS.get(classification, "blocked")


def is_valid(classification: str) -> bool:
    return classification in VALID_CLASSIFICATIONS


def agent_access(classification: str) -> dict[str, str]:
    """Return {agent: state} for a classification.

    An unknown value falls back to the strictest state per agent (blocked >
    hidden > available) instead of raising or borrowing a single table, so a
    corrupted registry can never open a gate that should be closed.
    """
    table = _AGENT_ACCESS.get(classification, _FALLBACK_ACCESS)
    return dict(table)
