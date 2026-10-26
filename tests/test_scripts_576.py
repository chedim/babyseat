"""#576 G: a stored command is a GRAPH -- sequences (&& || ; & newline), pipes,
groups ( ), redirects (2>&1, >, >>, <, heredocs), and argument nodes (literal,
param {{x}}, glob, $VAR, $( ) substitution, env prefix) -- that compiles back to
bash only to run. Each word keeps its raw shell text, so the round trip keeps the
meaning exactly (quoting and expansion included)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import scripts as S  # noqa: E402

CORPUS = [
    "cd {{path}} && python -I -m pytest {{target}} -x -q 2>&1 | tail -25",
    "gh pr view {{n}} --json title,body,files,mergedAt 2>&1 | head -60",
    "cd {{self.path}} && git status -sb | head -n 15 && git log --oneline -n 3 && git diff --stat | tail -5",
    "cd {{path}} && grep -rn --include='*.py' --exclude-dir=.git -e '{{pattern}}' .",
    "printf 'health %s\\n' \"$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:1/health)\"",
    "cd {{self.path}} && (nohup python -m pytest {{target}} -q > {{log}} 2>&1; echo $? > {{log}}.rc) &",
    "A=1 B='two words' env | grep -c A",
    "ls *.py | wc -l; echo done || echo failed",
    "cat <<'EOF' > /tmp/x.txt\nhello $NOT_EXPANDED\nEOF",
    "echo \"root [`echo nested`]\" >> out.log",
    "gh issue view {{number}} --json title,state,body --jq '\"\\(.title) [\\(.state)]\"'",
    "! false && echo negated",
    "sed -n '{{from}},{{to}}p' {{file}}\necho second line",
]


@pytest.mark.parametrize("cmd", CORPUS)
def test_the_round_trip_is_stable(cmd):
    g = S.parse(cmd)
    once = S.compile(g)
    assert S.parse(once) == g                       # compile(parse(x)) parses to the same graph


def test_the_graph_has_the_structure():
    g = S.parse("cd {{path}} && python -I -m pytest {{target}} -x -q 2>&1 | tail -25")
    first, second = g["items"]
    assert first["sep"] == "&&" and S.program(first["pipe"]["stages"][0]) == "cd"
    stages = second["pipe"]["stages"]
    assert [S.program(s) for s in stages] == ["python", "tail"]
    assert [r["raw"] for r in stages[0]["redirs"]] == ["2>&1"]
    kinds = {w["raw"]: w["kind"] for w in stages[0]["words"]}
    assert kinds["{{target}}"] == "param" and kinds["-x"] == "lit"


def test_word_kinds():
    g = S.parse("A=1 ls *.py \"$HOME/x\" $(date) {{p}} plain")
    st = g["items"][0]["pipe"]["stages"][0]
    assert [e["raw"] for e in st["env"]] == ["A=1"]
    kinds = [w["kind"] for w in st["words"]]
    assert kinds == ["lit", "glob", "var", "subst", "param", "lit"]


def test_groups_background_and_heredocs():
    g = S.parse("cd x && (nohup run > log 2>&1; echo $?) &")
    item = g["items"][1]
    assert item["sep"] == "&" and item["pipe"]["stages"][0]["group"] is not None
    h = S.parse("cat <<'EOF' > f\nbody $X\nEOF")
    st = h["items"][0]["pipe"]["stages"][0]
    assert st["redirs"][0]["raw"] == "<<'EOF'" and st["redirs"][0]["body"] == "body $X\nEOF"


@pytest.mark.parametrize("cmd", [
    "printf 'a %s\\n' \"$(echo b)\" | tr a-z A-Z",
    "A='x y' sh -c 'echo \"$A\"'",
    "echo one; false || echo two && echo three",
    "cat <<'EOF'\nliteral $HOME\nEOF",
    "echo \"q 'inner' $((1+2))\"",
    "(echo g1; echo g2) | sort -r",
])
def test_the_compiled_script_does_the_same(cmd):
    run = lambda c: subprocess.run(["bash", "-c", c], capture_output=True, text=True, timeout=10).stdout
    assert run(S.compile(S.parse(cmd))) == run(cmd)


@pytest.mark.parametrize("bad", ["echo 'unclosed", "echo \"$(unclosed\"", "( echo a", "echo a )", "cat <<EOF\nno end"])
def test_what_is_not_a_script_is_refused(bad):
    with pytest.raises(S.NotAScript):
        S.parse(bad)


# ---- wired into the seat ---------------------------------------------------------------------

import tempfile  # noqa: E402
import json  # noqa: E402

from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _obj(est, name="box"):
    e = QuantumEntity(eid=MEMBRANE_NS + f"Component:{name}", cls="Component", properties={"name": name})
    est.upsert(e)
    return est.docs[e.eid]


def _method(est, name):
    return next(d for d in est.docs.values() if (d.properties or {}).get("method_name") == name)


def test_a_defined_method_carries_its_graph_and_runs_from_it():
    est = _est()
    o = _obj(est)
    assert N.method_define(None, est, None, None, o.eid, "hi", "echo hi-{{who}} | tr a-z A-Z")["ok"]
    m = _method(est, "hi")
    g = m.properties["script"]
    assert [S.program(st) for st in S.stages(g)] == ["echo", "tr"]
    out = N.method_call(None, est, None, None, o.eid, "hi", {"who": "bob"})
    assert out["status"] == "ok" and "HI-BOB" in (out.get("head") or "")


def test_a_command_that_is_not_a_script_is_refused_at_define():
    est = _est()
    o = _obj(est)
    r = N.method_define(None, est, None, None, o.eid, "bad", "echo 'unclosed")
    assert not r["ok"] and "not a script" in r["error"]


def test_an_edited_command_is_parsed_again_and_a_broken_one_never_runs():
    est = _est()
    o = _obj(est)
    N.method_define(None, est, None, None, o.eid, "hi", "echo one")
    m = _method(est, "hi")
    m.properties["command"] = "echo two"                    # edited outside define
    assert "two" in N.method_call(None, est, None, None, o.eid, "hi", {}).get("head", "")
    m.properties["command"] = "echo 'broken"
    out = N.method_call(None, est, None, None, o.eid, "hi", {})
    assert out["status"] == "blocked" and "not a script" in json.dumps(out)


def test_the_migration_strips_programs_deletes_variants_and_keeps_methods(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    o = _obj(est)
    N.method_define(None, est, None, None, o.eid, "hi", "echo hi")
    prog = QuantumEntity(eid=MEMBRANE_NS + "shell:gh", cls="Component", properties={"name": "gh", "command": "gh"})
    var = QuantumEntity(eid=MEMBRANE_NS + "shell:gh-head-4d23", cls="Component",
                        properties={"name": "gh-head", "command": "gh pr view 1 | head"})
    est.upsert(prog); est.upsert(var)
    rep = S.migrate(est, backup_dir=tmp_path)
    assert rep["stripped"] == 1 and rep["deleted"] == 1
    assert "command" not in est.docs[prog.eid].properties and var.eid not in est.docs
    assert _method(est, "hi").properties["command"] == "echo hi"
    backup = json.loads(Path(rep["backup"]).read_text())
    assert var.eid in backup["deleted"] and backup["stripped"][prog.eid] == "gh"
    assert S.migrate(est, backup_dir=tmp_path)["deleted"] == 0           # once


def test_the_seat_migrates_at_boot():
    import inspect
    from membrane import opencode
    assert "_SCR.migrate(srv.entity_store)" in inspect.getsource(opencode)


# ---- #584 review -----------------------------------------------------------------------------

def test_a_command_entity_made_later_survives_every_boot(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    S.migrate(est, backup_dir=tmp_path)                        # the first boot
    tool = QuantumEntity(eid=MEMBRANE_NS + "Tool:deploy", cls="Tool",
                         properties={"name": "deploy", "command": "make deploy", "parents": [MEMBRANE_NS + "Tool:ci"]})
    est.upsert(tool)
    late = QuantumEntity(eid=MEMBRANE_NS + "shell:gh-view-1a2b", cls="Component",
                         properties={"name": "gh-view", "command": "gh pr view 2"})
    est.upsert(late)
    S.migrate(est, backup_dir=tmp_path)                        # every later boot: nothing
    assert est.docs[tool.eid].properties["command"] == "make deploy" and late.eid in est.docs


def test_only_generated_variants_are_deleted(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    for tail, cmd in (("git-lfs", "git lfs"), ("gh-head-4d23", "gh pr view | head"), ("net:seat-census", "lsof -i")):
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + f"shell:{tail}", cls="Component",
                                 properties={"name": tail, "command": cmd}))
    other = QuantumEntity(eid=MEMBRANE_NS + "Tool:build", cls="Tool", properties={"name": "build", "command": "make"})
    est.upsert(other)
    rep = S.migrate(est, backup_dir=tmp_path)
    assert rep["deleted"] == 1 and MEMBRANE_NS + "shell:gh-head-4d23" not in est.docs
    assert "command" not in est.docs[MEMBRANE_NS + "shell:git-lfs"].properties          # a program: kept, stripped
    assert "command" not in est.docs[MEMBRANE_NS + "shell:net:seat-census"].properties
    assert est.docs[other.eid].properties["command"] == "make"                         # outside shell:: untouched


def test_no_backup_no_change(tmp_path, monkeypatch):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    v = QuantumEntity(eid=MEMBRANE_NS + "shell:gh-head-4d23", cls="Component",
                      properties={"name": "x", "command": "gh pr view"})
    est.upsert(v)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_text", boom)
    rep = S.migrate(est, backup_dir=tmp_path)
    assert rep["deleted"] == 0 and rep["stripped"] == 0 and v.eid in est.docs
