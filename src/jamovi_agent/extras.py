"""Entry points jamovi looks up in ``jamovi.server.extras``.

jamovi 28 calls ``get_extra_handlers(session)`` once while building its main
web app and mounts each ``(path, handler)`` pair (any HTTP method).
``instance_for_open`` is looked up on every open request, so it must exist
once this module is installed; it keeps jamovi's default behaviour.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def get_extra_handlers(session):
    """Routes of the agent, relative to the jamovi main app root."""
    try:
        from .web import AgentWeb
        return AgentWeb(session).routes()
    except Exception:  # never break jamovi because of the agent
        log.exception("jamovi_agent: could not set up routes")
        return []


async def instance_for_open(session, options: dict):
    """Instance to open a data set into: always a new one (jamovi default)."""
    return await session.create(), False
