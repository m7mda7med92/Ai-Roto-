"""Feature modules. Each sub-package is independent and is discovered via :mod:`registry`.

A module package contains at least ``panel.py`` with a ``BaseModulePanel``
subclass. Business logic for future phases goes into separate files inside
the module package (e.g. ``camera_tracking/solver_client.py``) - never into
the panel - and heavy work always goes to the engine.
"""
