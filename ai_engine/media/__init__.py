"""Footage access for the engine (image sequences and video files).

Decoding happens only in the engine process (numpy / OpenCV / Pillow live in the
engine venv, never in Maya). Maya receives small JPEG proxies to display.
"""
