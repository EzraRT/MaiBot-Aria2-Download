"""Results shared by supported downloaders."""

from dataclasses import dataclass
from typing import Literal

from .magnets import Magnet


@dataclass(frozen=True)
class Submission:
    magnet: Magnet
    status: Literal["added", "accepted", "rejected", "unknown"]
    gid: str = ""  # Only aria2 returns a task ID on submission.
    error: str = ""


@dataclass(frozen=True)
class SubmissionSummary:
    """Batch counts without assuming which magnets were rejected or are pending."""

    added: int = 0
    accepted: int = 0
    rejected: int = 0
    unknown: int = 0
    errors: tuple[str, ...] = ()
