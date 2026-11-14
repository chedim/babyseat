"""Home Assistant's frontend formats every string of strings.json / translations/en.json as an ICU message: `<x>` reads as an HTML tag and
shows "translation error: INVALID_TAG" (the config form of the real instance, 2026-11-14); a `{a, b}` is a placeholder with a format. A string may hold
only plain text and simple `{name}` placeholders."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "custom_components" / "membrane"


def _strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}/{k}")
    elif isinstance(node, str):
        yield path, node


def test_no_string_holds_a_tag_or_a_formatted_placeholder():
    bad = []
    for name in ("strings.json", "translations/en.json"):
        for path, text in _strings(json.loads((ROOT / name).read_text())):
            if re.search(r"[<>]", text):
                bad.append(f"{name}{path}: an angle bracket reads as an HTML tag")
            for ph in re.findall(r"\{([^}]*)\}", text):
                if not re.fullmatch(r"[A-Za-z_]\w*", ph):
                    bad.append(f"{name}{path}: placeholder {{{ph}}} is not a plain name")
    assert not bad, "\n".join(bad)


def test_the_two_files_say_the_same():
    a = json.loads((ROOT / "strings.json").read_text())
    b = json.loads((ROOT / "translations/en.json").read_text())
    assert a == b
