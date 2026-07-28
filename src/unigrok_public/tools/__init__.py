"""Phase 3 SRP tool domains (chats, system, media-later).

Handlers stay registered on the FastMCP app in server.py; domain modules own
payload construction and pure session/chat helpers so server.py can shrink.
"""

from . import chats, system

__all__ = ["chats", "system"]
