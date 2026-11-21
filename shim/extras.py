"""jamovi.server.extras shim: delegates to the jamovi_agent package.

Installed at <jamovi>/server/jamovi/server/extras.py. jamovi imports
get_extra_handlers() and instance_for_open() from here if the module exists.
"""
from jamovi_agent.extras import get_extra_handlers, instance_for_open  # noqa: F401
