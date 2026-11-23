"""Single source of truth for toolkit and API versions."""

TOOLKIT_NAME = "Maya AI Production Toolkit"
TOOLKIT_ID = "MayaAIProductionToolkit"
TOOLKIT_VERSION = "0.3.0"

#: Bump when the HTTP contract changes in a backwards-incompatible way.
API_VERSION = "v1"
API_PREFIX = "/api/" + API_VERSION

#: Oldest Python the shared/plugin code must support (Maya 2024 = Python 3.10).
MIN_PLUGIN_PYTHON = (3, 10)
#: Oldest Python supported for the engine virtual environment.
MIN_ENGINE_PYTHON = (3, 10)
