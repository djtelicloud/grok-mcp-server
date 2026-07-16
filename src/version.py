"""Canonical UniGrok release version."""

__version__ = "0.6.0"

# Cache-bust token shared by every /ui asset reference (index.html links, the
# markdown.js import inside app.js, the Swarm script/sample pair, and the
# runtime handshake in app.js).
# Bump the -rN suffix whenever any /ui asset changes; a pytest asserts every
# copy of this string agrees so HTML and JS can never skew apart silently.
UI_ASSET_VERSION = "grok-v0.6.0-r11"
