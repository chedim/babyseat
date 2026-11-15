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


def test_the_token_texts_name_the_admin_token_file_and_no_role_wording():
    """No users (2026-11-14): every valid token is the admin. The texts say where the token is (`admin.token`, printed at start, or one minted with
    `--secret-file`) and never `--sub owner` or a per-role sentence."""
    for name in ("strings.json", "translations/en.json"):
        texts = dict(_strings(json.loads((ROOT / name).read_text())))
        for path, text in texts.items():
            assert not re.search(r"owner|--sub|operator", text, re.I), f"{name}{path}: {text}"
        form = texts["/config/step/connect/description"]
        reauths = [t for p, t in texts.items() if "reauth" in p and "reauth_inside" not in p and p.endswith("description")]
        assert len(reauths) == 2, reauths                     # the refused token, and the entry with no seat (an old inside entry with nothing to run)
        assert "admin.token" not in texts["/config/step/reauth_inside/description"]   # the seat Home Assistant runs: a restart, no token to paste
        for t in (form, *reauths):
            assert "admin.token" in t and "door.secret" in t, t


def test_no_string_holds_a_url_the_links_are_placeholders():
    """hassfest refuses a URL inside a translation string ("please use description placeholders instead"): a link is a `{docs_url}` placeholder
    the flow fills. The pattern is hassfest's own RE_URL (script/hassfest/translations.py, 2026-11-15)."""
    re_url = re.compile(r"(((ftp|ftps|scp|http|https|mqtt|mqtts|socket|socks5):\/\/|www\.)"
                        r"[a-z0-9]+([\-\.]{1}[a-z0-9]+)*\.[a-z]{2,5}(:[0-9]{1,5})?(\/.*)?)", re.IGNORECASE)
    for name in ("strings.json", "translations/en.json"):
        for path, text in _strings(json.loads((ROOT / name).read_text())):
            assert not re_url.search(text), f"{name}{path}: {text}"
