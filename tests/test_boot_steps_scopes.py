"""main()'s boot steps are named closures (docs/python-to-c1f.md). The dangerous mistake in carving them: a name one step
binds (as its own local) and another step reads (as a global that is never bound): the read fails at run time, and a
try/except around it hides it (live 2026-11-11: `_serve_socket`, bound in setup and rebound in serve, hung the port).
This checks the scopes statically: every step is a closure, and no name is a local of one step and an unbound global
of another."""
import symtable
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
STEPS = ["setup", "field_rows", "entities", "minds", "loops_goal_graph", "router", "embedder", "config", "serve"]


def _main_scope():
    top = symtable.symtable(SRC, "opencode.py", "exec")
    return next(c for c in top.get_children() if c.get_name() == "main")


def test_every_top_level_boot_step_is_a_closure_of_main():
    names = [c.get_name() for c in _main_scope().get_children()]
    for s in STEPS:
        assert f"_step_{s}" in names, s


def test_no_name_is_a_local_of_one_step_and_an_unbound_global_of_another():
    main = _main_scope()
    steps = [c for c in main.get_children() if c.get_name().startswith("_step_")]
    main_bound = {s.get_name() for s in main.get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace()}
    local_of, global_of = {}, {}
    for st in steps:
        for sym in st.get_symbols():
            n = sym.get_name()
            if sym.is_local() and not sym.is_nonlocal() and (sym.is_assigned() or sym.is_imported() or sym.is_namespace()):
                local_of.setdefault(n, set()).add(st.get_name())
            if sym.is_global() and sym.is_referenced() and n not in main_bound:
                global_of.setdefault(n, set()).add(st.get_name())
    clash = {n: (local_of[n], global_of[n]) for n in local_of if n in global_of and not n.startswith("__")}
    # a builtin or a module-level name is a global of every function: only names a step binds count
    import builtins
    mod = {s.get_name() for s in symtable.symtable(SRC, "opencode.py", "exec").get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace()}
    clash = {n: v for n, v in clash.items() if n not in mod and not hasattr(builtins, n)}
    assert not clash, clash


def test_the_warm_up_jobs_start_after_the_serve_is_up_not_during_the_boot():
    main = SRC[SRC.index("\ndef main() -> None:"):]
    assert 'lambda: recall_vote.warm(srv.efield)' in main and "\n                    recall_vote.warm(srv.efield)" not in main
    assert '_defer("forewarning", srv._forewarn.start)' in main and "\n                    srv._forewarn.start()" not in main
    assert main.rindex("_run_deferred()") > main.index("_boot_door.handover()")        # after the serve took the socket
