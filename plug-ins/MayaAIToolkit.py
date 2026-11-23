"""Maya plug-in shim. The real implementation lives in maya_plugin/plugin_entry.py.

The module file adds the toolkit root to PYTHONPATH, so the package import works.
"""
from maya_plugin.plugin_entry import (  # noqa: F401
    initializePlugin,
    maya_useNewAPI,
    uninitializePlugin,
)
