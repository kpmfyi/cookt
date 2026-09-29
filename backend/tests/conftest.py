"""Shared pytest configuration.

Opt-in markers (skipped by default):
- ``local_model``: calls the local llama-swap endpoint; run with ``--run-local-model``.
- ``network``: fetches real URLs; run with ``--run-network``.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-local-model",
        action="store_true",
        default=False,
        help="run tests marked local_model (live llama-swap calls)",
    )
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="run tests marked network (real HTTP fetches)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    gates = {
        "local_model": ("--run-local-model", "opt-in: pass --run-local-model"),
        "network": ("--run-network", "opt-in: pass --run-network"),
    }
    for marker, (option, reason) in gates.items():
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
