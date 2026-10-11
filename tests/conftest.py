"""Suite-wide: the offline probe (membrane/offline.py) stays off -- a test seat's
backend is a fake, and a 'walk' declared mid-test would fail its embeds."""
import os

os.environ.setdefault("MEMBRANE_OFFLINE_PROBE", "0")
