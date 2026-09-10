"""THE BOOT-ORDER GUARD (the operator's socket-closed regression): the
`if __name__ == "__main__": main()` guard MUST be the LAST statement in
opencode.py. If any def/assignment sits AFTER the guard, script mode
(python -m membrane.opencode) boots main() and blocks in the serve loop
before those defs ever bind -- the handlers then NameError at call time,
the request thread dies, and the client sees 'socket connection closed
unexpectedly'. This exact bug shipped in the speech lane (30c6db8):
_speak_block was defined after the guard, every chat turn 500'd, and the
seat was unusable while imports/pytest stayed green (imports never fire
the guard)."""

import ast
import pathlib

_MODULE = pathlib.Path(__file__).resolve().parent.parent / "membrane" / "opencode.py"


def test_main_guard_is_last_statement():
    tree = ast.parse(_MODULE.read_text())
    body = tree.body
    assert body, "opencode.py parsed to an empty module"
    last = body[-1]
    assert isinstance(last, ast.If), (
        f"the __main__ guard must be the module's last statement; "
        f"found {ast.dump(last)[:120]}")
    test = last.test
    assert isinstance(test, ast.Compare) and len(test.comparators) == 1
    left = test.left
    ok = (isinstance(left, ast.Name) and left.id == "__name__"
          and isinstance(test.comparators[0], ast.Constant)
          and test.comparators[0].value == "__main__")
    assert ok, "the final statement must be `if __name__ == '__main__':`"
    assert any(
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
        for stmt in last.body), "the guard must call main()"


def test_no_defs_after_the_guard():
    """Belt-and-braces: even a trailing bare def (a syntax-legal cousin of
    the bug) must fail the boot-order check."""
    tree = ast.parse(_MODULE.read_text())
    guard_idx = next(i for i, n in enumerate(tree.body)
                     if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                     and getattr(n.test.left, "id", "") == "__name__")
    trailing = tree.body[guard_idx + 1:]
    assert not trailing, (
        f"{len(trailing)} statement(s) after the __main__ guard bind too "
        f"late in script mode: "
        f"{[type(n).__name__ for n in trailing]}")
