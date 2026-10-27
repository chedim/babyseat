"""Issue #128: a command entity whose template already wraps a placeholder in
quotes (`gh issue create --body "{{body}}"`) had the value shell-quoted a
SECOND time by substitute_args/_fill, so a body with an apostrophe, newline
or paren produced broken shell (rc=2, "unexpected EOF"). The interpolation
now strips the template's own surrounding quote pair, so a value is quoted
exactly once -- an argument is data, never shell.

#614 C: the text templates retired; a method's code is a tree whose {{name}}s
are param nodes, and lang.bind fills them by the same rule."""

import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lang as LG  # noqa: E402


def _bind(tpl, values):
    return LG.bind(LG.parse("sh", tpl, params=True), lambda k: values.get(k), LG.QUOTE["sh"])


_TPL = 'gh issue create --title "{{title}}" --body "{{body}}"'


class TestParamQuoting(unittest.TestCase):
    def test_quoted_placeholder_is_not_double_quoted(self):
        out = _bind(_TPL, {"title": "a bug", "body": "plain text"})[0]
        self.assertEqual(shlex.split(out),
                         ["gh", "issue", "create", "--title", "a bug",
                          "--body", "plain text"])

    def test_apostrophe_value_stays_valid_shell(self):
        out = _bind('gh issue create --body "{{body}}"', {"body": "doesn't parse"})[0]
        self.assertEqual(shlex.split(out),
                         ["gh", "issue", "create", "--body", "doesn't parse"])

    def test_specials_and_newline_are_quoted_once(self):
        body = "has (parens) $HOME and `tick`\nand a newline"
        out = _bind('echo --body "{{body}}"', {"body": body})[0]
        self.assertEqual(shlex.split(out), ["echo", "--body", body])

    def test_bare_placeholder_still_quotes_the_value(self):
        out = _bind("echo {{word}}", {"word": "two; rm -rf /tmp/x"})[0]
        self.assertEqual(shlex.split(out), ["echo", "two; rm -rf /tmp/x"])

    def test_missing_placeholder_is_left_verbatim(self):
        out, missing = _bind(_TPL, {"title": "a bug"})
        self.assertIn("{{body}}", out)
        self.assertEqual(missing, ["body"])

    def test_single_quoted_placeholder_is_not_double_quoted(self):
        out, missing = _bind("gh issue create --title '{{title}}'", {"title": "a bug"})
        self.assertEqual(missing, [])
        self.assertEqual(shlex.split(out),
                         ["gh", "issue", "create", "--title", "a bug"])


if __name__ == "__main__":
    unittest.main()
