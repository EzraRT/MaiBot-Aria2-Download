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
