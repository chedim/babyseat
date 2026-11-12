"""Tests that need a card answered use the real thing: the decisions pack on a live field engine (membrane/packs/decisions.c1f).
`answer` has the signature the Python answer had (est, ledger, spec, value, conv, message, work, tell, relayed); the engine is
made on first use for that entity store and stopped at exit. `relay(srv, spec, value, quote)` is the model's relay."""
import atexit
import os
import sys
import time
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402

import contextlib


@contextlib.contextmanager
def running(est, ledger=None, srv=None):
    """The decisions pack's engine for this store, for the duration of one call (the adapters are process-wide: nothing
    stays running for the next test)."""
    srv = srv if srv is not None else getattr(est, "_srv", None)
    if srv is None:
        srv = types.SimpleNamespace(entity_store=est)
        est._srv = srv
    srv.entity_store = est
    if ledger is not None:
        srv.user_ledger = ledger
    if not hasattr(srv, "events_pool"):
        srv.events_pool = WA.WatchEvents()
    for k, v in (("membrane", None), ("_loop_runner", types.SimpleNamespace(sessions={}, hub=None))):
        if not hasattr(srv, k):
            setattr(srv, k, v)
    os.environ["MEMBRANE_FIELDS"] = "1"
    here = os.getcwd()
    led = str(getattr(getattr(srv, "user_ledger", None), "path", "") or "")
    if led:
        os.chdir(os.path.dirname(os.path.abspath(led)))      # the seat's folder holds its ledger (the drivers refuse /tmp elsewhere)
    FL.start(srv)
    AD.start(srv)
    try:
        FL.boot_started(est, ledger=str(getattr(getattr(srv, "user_ledger", None), "path", "") or "") or None)
        time.sleep(0.15)
        yield srv
        AD.drain(timeout=5.0)                   # a card's event, its ledger line: done before the test looks
        time.sleep(0.1)
    finally:
        AD.stop()
        FL.stop(srv)
        os.chdir(here)


def answer(est, ledger, spec, value, conv="", message="", work=None, tell=True, relayed=None):
    with running(est, ledger) as srv:
        req = {"value": value}
        for k, v in (("conv", conv), ("message", message)):
            if v:
                req[k] = v
        if work is not None:
            req["work"] = work
        if tell is False:
            req["tell"] = False
        if relayed:
            req["relayed"] = relayed
        return _REAL_DOOR(srv, spec, req)


def relay(srv, spec, value, quote):
    est = srv.entity_store
    ent = est.resolve(str(spec), exact=True)
    with running(est):
        d = est.docs[ent.eid]
        before = (d.properties or {}).get("answer_result"), (d.properties or {}).get("relay_result")
        d.properties = {**(d.properties or {}), "relay": {"value": value, "quote": quote}}
        est.upsert(d, embedder=None)
        t = time.time() + 10
        while time.time() < t:
            p = est.docs[ent.eid].properties
            ar, rr = p.get("answer_result"), p.get("relay_result")
            if ar and ar != before[0]:
                return ar
            if rr and rr != before[1] and rr.get("ok") is False:
                return rr
            time.sleep(0.03)
    return {"ok": False, "error": "no answer in time"}


_REAL_DOOR = FL.real_answer_card


def door(srv, card, request, timeout=15.0):
    """The seat's answer door with the decisions engine running for the duration of the call (tests whose subject is the
    conversation graph or a route, not the pack)."""
    with running(srv.entity_store, getattr(srv, "user_ledger", None), srv=srv):
        return _REAL_DOOR(srv, card, request, timeout)
