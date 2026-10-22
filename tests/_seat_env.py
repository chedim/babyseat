"""The environment a test boots a real seat subprocess with (#408).

A seat inherits os.environ, and with it the operator's home and data dir:
it registered itself in ~/.membrane/peers.jsonl (the live seat's peer
roster), read the home's commons and auth files, and followed an inherited
MEMBRANE_DATA_DIR. Booted with this env (and cwd=seat_dir) it writes only
under seat_dir, and runs THIS checkout's membrane.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def seat_env(seat_dir, base=None) -> dict:
    seat_dir = str(seat_dir)
    env = dict(os.environ if base is None else base)
    home = os.path.join(seat_dir, "home")
    os.makedirs(home, exist_ok=True)
    env["HOME"] = home
    env["MEMBRANE_DATA_DIR"] = seat_dir
    env["MEMBRANE_PEERS_PATH"] = os.path.join(seat_dir, "peers.jsonl")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (ROOT, env.get("PYTHONPATH")) if p)
    return env
