"""Shared, dependency-free code used by both the Maya plugin and the AI engine.

Everything in this package must:
  * run on the oldest supported interpreter (Maya 2024 bundles Python 3.10),
  * use only the Python standard library,
  * never import Maya, Qt, or any AI/ML library.

That keeps the wire contracts identical on both sides of the HTTP boundary.
"""
from shared.version import TOOLKIT_NAME, TOOLKIT_VERSION, API_VERSION, API_PREFIX

__all__ = ["TOOLKIT_NAME", "TOOLKIT_VERSION", "API_VERSION", "API_PREFIX"]
