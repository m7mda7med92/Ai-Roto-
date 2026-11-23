"""Test doubles. ``tests/mocks/maya`` is a *fake* Maya package used when real Maya is unavailable.

Tests that use it are MOCK MAYA tests: they prove the plugin's Python logic and
Qt UI under a Maya-like runtime (Python 3.10 + PySide2 5.15), not behaviour
inside real Autodesk Maya.
"""
