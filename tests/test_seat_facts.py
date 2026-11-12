"""The launcher's facts (seat_facts): what packs read from Boot:seat, assembled from the settings, the environment and the seat."""
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import fields_live as FL  # noqa: E402

KNOWN = {"ui_dir": "/seat/ui", "build_id": "b1", "seat_version": "1.0", "seat_cwd": "/seat", "seat_home": "/home/p",
         "seat_project": "membrane"}


def _srv(router=True, models=None):
    rec = lambda n, b, k: types.SimpleNamespace(name=n, backend=b, kind=k)    # noqa: E731
    recs = {"m1": rec("m1", "", "local"), "c1": rec("c1", "openrouter", "cloud"), "c2": rec("c2", "claude-agent", "cloud")}
    return types.SimpleNamespace(backend="http://localhost:11434", router=types.SimpleNamespace(records=recs) if router else None,
                                 _loop_runner=types.SimpleNamespace(model="qwen"), idle_cortex_model="q2",
                                 default_seat_model="q3", _workers_model="", think=None, _minds_auth={})


def test_the_whole_set_from_real_looking_sources():
    st = {"name": "Rosie", "aliases": ["Ro"], "stt": "http://127.0.0.1:8080", "voice_out": True, "language": "de"}
    f = FL.seat_facts({"MEMBRANE_DOOR_PORT": "4097", "MEMBRANE_BLURP": "1"}, st, _srv(), KNOWN)
    assert f == {**KNOWN, "door_port": 4097, "door_secret_file": "/seat/.membrane/jwt.secret",
                 "seat_name": "Rosie", "seat_named": True, "seat_aliases": ["Ro"], "seat_stt": "http://127.0.0.1:8080",
                 "seat_voice_out": True, "seat_language": "de", "seat_blurp": True, "seat_backend": "http://localhost:11434",
                 "router_on": True,
                 "cloud_models": [{"name": "c1", "backend": "openrouter"}, {"name": "c2", "backend": "claude-agent"}],
                 "minds": {"loops": "qwen", "cortex": "q2", "seat_default": "q3", "workers": "", "loops_auth": "chat",
                           "cortex_auth": "chat", "workers_auth": "chat", "think": "auto"}}


def test_no_door_port_without_the_env_var_or_with_junk():
    for env in ({}, {"MEMBRANE_DOOR_PORT": ""}, {"MEMBRANE_DOOR_PORT": "x"}, {"MEMBRANE_DOOR_PORT": "0"}):
        assert "door_port" not in FL.seat_facts(env, {}, _srv(), KNOWN)


def test_the_secret_is_a_path_never_the_secret():
    f = FL.seat_facts({"MEMBRANE_JWT_SECRET_FILE": "/etc/j.secret"}, {}, _srv(), KNOWN)
    assert f["door_secret_file"] == "/etc/j.secret"
    f = FL.seat_facts({"MEMBRANE_JWT_SECRET": "s" * 40}, {}, _srv(), KNOWN)
    assert "door_secret_file" not in f and "s" * 40 not in repr(f)
    assert "door_secret_file" not in FL.seat_facts({"MEMBRANE_JWT": "0"}, {}, _srv(), KNOWN)


def test_a_missing_source_omits_the_fact_never_none():
    f = FL.seat_facts({}, {}, types.SimpleNamespace(), {})
    assert None not in f.values()
    for k in ("seat_backend", "cloud_models", "ui_dir", "build_id", "door_secret_file", "seat_stt"):
        assert k not in f
    assert f["router_on"] is False and f["seat_named"] is False and f["seat_blurp"] is False and f["seat_language"] == "en"
    assert f["seat_name"] == "Bob" and f["seat_aliases"] == [] and f["seat_voice_out"] is False


def test_blurp_off_values():
    for v in ("0", "", "off", "false", "no"):
        assert FL.seat_facts({"MEMBRANE_BLURP": v}, {}, _srv(), KNOWN)["seat_blurp"] is False
    assert FL.seat_facts({}, {}, _srv(), KNOWN)["seat_blurp"] is False


def test_no_router_means_off_and_no_rungs():
    f = FL.seat_facts({}, {}, _srv(router=False), KNOWN)
    assert f["router_on"] is False and "cloud_models" not in f


class _Est:
    def __init__(self):
        self.docs = {}
    def upsert(self, d, embedder=None):
        self.docs[d.eid] = d


def test_boot_started_writes_each_fact_and_the_dict_and_late_writes_do_not_announce():
    est = _Est()
    FL.boot_started(est, facts={"seat_name": "Bob", "router_on": False})
    p = est.docs["Boot:seat"].properties
    assert p["seat_name"] == "Bob" and p["facts"] == {"seat_name": "Bob", "router_on": False} and "started" in p
    t = p["started"]
    FL.boot_started(est, facts={"router_on": True}, announce=False)
    p = est.docs["Boot:seat"].properties
    assert p["router_on"] is True and p["facts"] == {"seat_name": "Bob", "router_on": True} and p["started"] == t
