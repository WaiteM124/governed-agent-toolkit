"""Shared pytest fixtures for the governed-agent-toolkit test suite."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Allow tests to import the package modules from the project root.
sys.path.insert(0, str(Path(__file__).parent.parent))


class FrozenClock:
    """
    Deterministic clock for injecting into ApprovalGateway.

    Advance `clock.now` directly in tests that need to simulate elapsed time.
    Using clock injection instead of patching datetime.now() means the
    production code path is exercised with a real datetime object — only the
    source of "now" is substituted.
    """

    def __init__(self) -> None:
        self.now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock()
