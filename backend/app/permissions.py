"""Role-based access: each role gives none / read / write on three areas.

  repositories - Repositories and their analyses (register repos, run / delete analyses, browse results),
                 pull-request impact runs and the feature catalogue
  insights     - Chat with Analyst and Repository Comparison
  settings     - Settings (LLM, users, user roles, database, audit log)

"none" hides the area, "read" shows it without changes, "write" allows add / run / delete as well.
Every API request is checked here by its path and method (see `rule_for`), so a route cannot be missed.
"""

from app.errors import AppError

AREAS = ("repositories", "insights", "settings")
LEVELS = {"none": 0, "read": 1, "write": 2}

ADMIN_ROLE_ID = "administrator"
PREDEFINED_ROLES = [
    {
        "role_id": "viewer",
        "name": "Viewer",
        "description": "Browses analyses and chats with the analyst.",
        "repositories": "read",
        "insights": "read",
        "settings": "none",
    },
    {
        "role_id": "analyst",
        "name": "Analyst",
        "description": "Registers repositories, runs analyses and comparisons.",
        "repositories": "write",
        "insights": "write",
        "settings": "none",
    },
    {
        "role_id": ADMIN_ROLE_ID,
        "name": "Administrator",
        "description": "Can do everything, including settings.",
        "repositories": "write",
        "insights": "write",
        "settings": "write",
    },
]


def permissions_of(row: dict | None) -> dict[str, str]:
    return {area: (row or {}).get(area) if (row or {}).get(area) in LEVELS else "none" for area in AREAS}


def can(user: dict, area: str, need: str = "read") -> bool:
    level = (user.get("permissions") or {}).get(area, "none")
    return LEVELS.get(level, 0) >= LEVELS[need]


def require(user: dict, area: str, need: str = "read") -> None:
    if not can(user, area, need):
        raise AppError(403, "Forbidden")


_REPOSITORIES = {"repositories", "analyses", "git", "impact-runs", "features"}
_INSIGHTS_READ_ONLY = {"chat"}  # chatting is a read of the stored analysis
_INSIGHTS = {"comparisons", "landscapes"}
_SETTINGS = {"settings", "users", "roles", "audit", "database"}
# Changing requests that only read: checking access to a remote repository.
_READ_POSTS = {"git/check", "git/branches"}


def rule_for(method: str, path: str) -> list[list[tuple[str, str]]]:
    """A list of requirements that must all hold, each met by any one (area, level) option. Empty: any signed-in user."""
    route = path.split("/api/", 1)[-1].strip("/")
    first = route.split("/", 1)[0]
    reading = method in ("GET", "HEAD", "OPTIONS") or (first == "landscapes" and route.endswith("/explain"))  # explaining a link only reads
    if first == "git":
        if route.startswith("git/credentials") or route.startswith("git/oauth"):
            return []  # personal git connections
        return [[("repositories", "read" if reading or route in _READ_POSTS else "write")]]
    if first in _REPOSITORIES:
        return [[("repositories", "read" if reading else "write")]]
    if first in _INSIGHTS_READ_ONLY:
        return [[("insights", "read")], [("repositories", "read")]]
    if first in _INSIGHTS:
        return [[("insights", "read" if reading else "write")], [("repositories", "read")]]
    if first in _SETTINGS:
        return [[("settings", "read" if reading else "write")]]
    return []  # dashboard, health


def check_request(user: dict, method: str, path: str) -> None:
    for options in rule_for(method, path):
        if not any(can(user, area, level) for area, level in options):
            raise AppError(403, "Forbidden")
