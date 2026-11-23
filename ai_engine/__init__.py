"""Maya AI Production Toolkit - independent background processing engine.

Runs in its own Python virtual environment, never inside Maya. Exposes a
token-authenticated HTTP API bound exclusively to 127.0.0.1.
"""
from shared.version import TOOLKIT_VERSION as ENGINE_VERSION

__all__ = ["ENGINE_VERSION"]
