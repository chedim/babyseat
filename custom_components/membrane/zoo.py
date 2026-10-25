"""THE BLURP ZOO (#283, the operator: "several seats in one Home Assistant,
each with its own Blurp").

The integration keeps a LIST of seats in its options (options["seats"]:
{id, name, host, serve_port, proxy_port, token, [user], [url], [stt_url]};
inside mode's own child is one of them, {id: "inside", inside: True}) and ONE
active seat for the whole house (options["active"]). The house's connection
-- comp["serve_url"/"serve_user"/"serve_pass"/"serve_ready"], what the relay,
the field sensors, the Assist agent and Home Assistant access read -- is the
active seat's (apply). Switching (switch) re-points it live and persists the
choice; the update listener skips the reload a switch alone would cause.

The inside seat's child writes into its own slot (SeatSlot): the house mirrors
it only while that seat is active, so a respawning child never moves a house
that is talking to the laptop.

The zoo's cards (fan_out): every seat's GET /membrane/self/card (its Blurp's
name, level, rung, mode, last activity) in parallel, each capped at a few
seconds; a seat without that door yet answers from /membrane/self/sheet's
header (+ /membrane/self/recent for the last activity).

No Home Assistant import here (the tests load it bare).
"""
from __future__ import annotations

import asyncio
import base64
import re
from urllib.parse import urlsplit

from .const import CONF_MODE, CONF_SERVE_PASS, CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_NAME, DEFAULT_SERVE_USER, DOMAIN, MODE_INSIDE

SEATS = "seats"
ACTIVE = "active"
INSIDE_ID = "inside"
INSIDE_NAME = "this Home Assistant"
DEFAULT_SERVE_PORT = 4096
DEFAULT_PROXY_PORT = 11500
CARD_TIMEOUT_S = 2.5            # a card per seat; a seat that takes longer is asleep to the zoo
ASLEEP_MODES = ("off", "asleep")
SEAT_KEYS = ("serve_url", "serve_user", "serve_pass", "serve_ready")


# ---------------------------------------------------------------- the list

def slug(*parts) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(str(p) for p in parts if p not in (None, "")).lower()).strip("-") or "seat"


def seat_url(seat: dict) -> str:
    """The seat's serve surface: its own URL (a migrated seat behind a proxy),
    else http://host:serve_port."""
    if seat.get("url"):
        return str(seat["url"]).rstrip("/")
    host = str(seat.get("host") or "").strip()
    return f"http://{host}:{int(seat.get('serve_port') or DEFAULT_SERVE_PORT)}" if host else ""


def display_name(seat: dict) -> str:
    return str(seat.get("name") or "").strip() or str(seat.get("host") or "").strip() or str(seat.get("id") or "seat")


def names(seats: list) -> list[str]:
    """One name per seat, never empty, never twice (the select's options)."""
    out, seen = [], set()
    for s in seats:
        n = display_name(s)
        if n in seen:
            n = f"{n} ({s.get('host') or s.get('id')})"
        seen.add(n)
        out.append(n)
    return out


def find(seats: list, sid) -> dict | None:
    return next((s for s in seats or [] if s.get("id") == sid), None)


def active_of(seats: list, sid) -> dict | None:
    """The active seat: the chosen one, else the first."""
    return find(seats, sid) or (seats[0] if seats else None)


def settings_of(options: dict) -> dict:
    """Everything but the active seat: a change here needs a reload, a switch doesn't."""
    return {k: v for k, v in (options or {}).items() if k != ACTIVE}


def migrate(data: dict, options: dict) -> dict:
    """The options with a seat list: an entry from before #283 (one seat, in its
    data) becomes a one-seat list that connects exactly as it did. Idempotent."""
    opts = dict(options or {})
    if isinstance(opts.get(SEATS), list) and opts[SEATS]:
        opts.setdefault(ACTIVE, opts[SEATS][0].get("id"))
        return opts
    if (data or {}).get(CONF_MODE) == MODE_INSIDE:
        seat = {"id": INSIDE_ID, "name": INSIDE_NAME, "inside": True}
    else:
        url = str((data or {}).get(CONF_SERVE_URL) or "").rstrip("/")
        parts = urlsplit(url)
        token = opts[CONF_SERVE_PASS] if CONF_SERVE_PASS in opts else (data or {}).get(CONF_SERVE_PASS)
        seat = {"id": slug(parts.hostname, parts.port) if parts.hostname else "seat", "name": "",
                "host": parts.hostname or "", "serve_port": parts.port or (443 if parts.scheme == "https" else 80),
                "proxy_port": DEFAULT_PROXY_PORT, "token": str(token or ""),
                "user": str((data or {}).get(CONF_SERVE_USER) or DEFAULT_SERVE_USER), "url": url}
    opts.pop(CONF_SERVE_PASS, None)                 # the token lives on its seat now
    opts[SEATS] = [seat]
    opts[ACTIVE] = seat["id"]
    return opts


def seat_from_form(form: dict, seats: list, old: dict | None = None) -> dict:
    """A seat from the options form (add, or edit `old`): host + ports + token,
    the name optional. An edit that moves the host or port drops a migrated URL."""
    host = str(form.get("host") or "").strip()
    port = int(form.get("serve_port") or DEFAULT_SERVE_PORT)
    if old is None:
        base = sid = slug(host, port)
        n = 2
        while find(seats, sid):
            sid, n = f"{base}-{n}", n + 1
        seat = {"id": sid}
    else:
        seat = dict(old)
        if (host, port) != (old.get("host"), old.get("serve_port")):
            seat.pop("url", None)
    seat.update({"name": str(form.get("name") or "").strip(), "host": host, "serve_port": port,
                 "proxy_port": int(form.get("proxy_port") or DEFAULT_PROXY_PORT)})
    if "token" in form or old is None:
        seat["token"] = str(form.get("token") or "")
    if form.get("user"):
        seat["user"] = str(form["user"])
    if form.get("stt_url"):
        seat["stt_url"] = str(form["stt_url"])
    else:
        seat.pop("stt_url", None)
    return seat


def remove(options: dict, sid) -> dict:
    """The options without seat `sid` -- never the last seat, never HA's own child;
    the active one gone, the house moves to the first."""
    seats = list(options.get(SEATS) or [])
    s = find(seats, sid)
    if s is None or s.get("inside") or len(seats) < 2:
        return options
    out = dict(options)
    out[SEATS] = [x for x in seats if x.get("id") != sid]
    if out.get(ACTIVE) == sid:
        out[ACTIVE] = out[SEATS][0]["id"]
    return out


# ---------------------------------------------------------------- the active seat

class SeatSlot(dict):
    """The inside seat's own connection (the supervisor writes here); `live`
    while it is the active seat -- then every write reaches the house too."""

    def __init__(self, comp: dict):
        super().__init__()
        self.comp, self.live = comp, False

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        if self.live:
            self.comp[k] = v


def conn_of(comp: dict, seat: dict) -> dict:
    """{serve_url, serve_user, serve_pass, serve_ready} for one seat (the inside
    one's from its child's slot)."""
    if seat.get("inside"):
        slot = comp.get("inside_slot") or {}
        return {"serve_url": str(slot.get("serve_url") or ""), "serve_user": str(slot.get("serve_user") or ""),
                "serve_pass": str(slot.get("serve_pass") or ""), "serve_ready": bool(slot.get("serve_ready"))}
    url = seat_url(seat)
    return {"serve_url": url, "serve_user": str(seat.get("user") or DEFAULT_SERVE_USER),
            "serve_pass": str(seat.get("token") or ""), "serve_ready": bool(url)}


def apply(comp: dict, seat: dict) -> None:
    """Point the house at `seat`: the connection everything reads, the speech
    server, the name; Assist's chats belonged to the old seat."""
    slot = comp.get("inside_slot")
    if isinstance(slot, SeatSlot):
        slot.live = bool(seat.get("inside"))
    comp.update(conn_of(comp, seat))
    if seat.get("inside") and slot is not None:
        for k in ("name", "aliases"):
            if k in slot:
                comp[k] = slot[k]
    elif not seat.get("inside"):
        comp["name"], comp["aliases"] = DEFAULT_NAME, []   # a seat elsewhere: until it says its own (fetch_name)
    comp["stt_url"] = str(seat.get("stt_url") or comp.get("stt_default") or "")
    comp["active_seat"] = seat.get("id")
    comp["seat_name"] = display_name(seat)
    comp["sessions"] = {}


def switch(hass, entry, sid) -> bool:
    """Make `sid` the house's seat: applied live (no reload), persisted in the
    entry's options; the sensors re-read and the listeners (the select) hear it."""
    comp = hass.data.get(DOMAIN) or {}
    seats = list((entry.options or {}).get(SEATS) or comp.get(SEATS) or [])
    seat = find(seats, sid)
    if seat is None:
        return False
    apply(comp, seat)
    opts = dict(entry.options or {})
    opts[ACTIVE] = sid
    comp["applied_opts"] = settings_of(opts)     # the update listener: nothing to reload
    hass.config_entries.async_update_entry(entry, options=opts)
    coord = comp.get("coordinator")
    if coord is not None:
        hass.async_create_task(coord.async_request_refresh())
    for cb in list(comp.get("on_switch") or []):
        cb()
    return True


# ---------------------------------------------------------------- the cards

def card_of(raw, recent=None) -> dict:
    """A seat's card -> {name, level, rung_name, mode, state, last_activity}:
    /membrane/self/card's own fields ({name, level, rung_name, next, mode,
    awake, last_activity: ISO | null, generated}), or /membrane/self/sheet's header (the
    last activity then from /membrane/self/recent's newest line)."""
    raw = raw if isinstance(raw, dict) else {}
    h = raw.get("header") if isinstance(raw.get("header"), dict) else raw
    mode = h.get("mode")
    last = h.get("last_activity")
    if last is None and recent:
        last = (recent[0] or {}).get("ts") if isinstance(recent[0], dict) else None
    awake = h.get("awake")                          # the card's own word (#285); the sheet's: its mode
    if not isinstance(awake, bool):
        awake = mode not in ASLEEP_MODES
    return {"name": h.get("name"), "level": h.get("level"), "rung_name": h.get("rung_name"), "mode": mode,
            "state": "awake" if awake else "asleep",
            "last_activity": last}


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user or DEFAULT_SERVE_USER}:{password or ''}".encode()).decode()


async def _get(sess, conn: dict, path: str):
    async with sess.get(conn["serve_url"] + path,
                        headers={"Authorization": _basic(conn["serve_user"], conn["serve_pass"])}) as r:
        if r.status != 200:
            return r.status, None
        return 200, await r.json(content_type=None)


async def fetch_card(sess, conn: dict) -> dict:
    """{card} or raises: the card door first, the sheet when the seat has none yet."""
    st, card = await _get(sess, conn, "/membrane/self/card")
    if st == 200:
        return card_of(card)
    if st not in (404, 405):
        raise RuntimeError(f"the seat answered {st}")
    st, sheet = await _get(sess, conn, "/membrane/self/sheet")
    if st != 200:
        raise RuntimeError(f"the seat answered {st}")
    try:
        _, rec = await _get(sess, conn, "/membrane/self/recent?n=1")
    except Exception:                                # noqa: BLE001 (the last activity is optional)
        rec = None
    return card_of(sheet, (rec or {}).get("lines") if isinstance(rec, dict) else None)


async def fetch_name(sess, comp: dict, timeout_s: float = CARD_TIMEOUT_S) -> None:
    """A seat elsewhere is called by its own name (issue #6): what its /ui/conf
    says (a standalone seat's page config, a plain serve's name + aliases);
    a seat that doesn't answer keeps the default apply() gave it. The inside
    seat's comes from its child."""
    seat = find(comp.get(SEATS) or [], comp.get("active_seat"))
    if not comp.get("serve_url") or (seat or {}).get("inside"):
        return
    url = comp["serve_url"]
    try:
        st, conf = await asyncio.wait_for(_get(sess, comp, "/ui/conf"), timeout_s)
    except Exception:                                # noqa: BLE001 (the name is optional)
        return
    name = str((conf or {}).get("name") or "").strip() if st == 200 and isinstance(conf, dict) else ""
    if name and comp.get("serve_url") == url:        # still the seat that answered
        comp["name"], comp["aliases"] = name, [str(a) for a in conf.get("aliases") or []]


async def _row(sess, comp, seat, active, timeout_s) -> dict:
    row = {"id": seat.get("id"), "name": display_name(seat), "host": seat.get("host") or "",
           "inside": bool(seat.get("inside")), "active": seat.get("id") == active, "online": False}
    conn = conn_of(comp, seat)
    if not conn["serve_url"]:
        row["error"] = "starting"
        return row
    try:
        card = await asyncio.wait_for(fetch_card(sess, conn), timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        row["error"] = "timeout"
        return row
    except Exception as e:                           # noqa: BLE001
        row["error"] = str(e) or type(e).__name__
        return row
    row["online"], row["card"] = True, card
    if not str(seat.get("name") or "").strip() and not seat.get("inside") and card.get("name"):
        row["name"] = str(card["name"])             # no name given: the seat's Blurp's own
    return row


async def fan_out(sess, comp: dict, seats: list, active, timeout_s: float = CARD_TIMEOUT_S) -> list:
    """Every seat's row, in the list's order, in parallel: {id, name, host,
    active, online, card | error}."""
    return list(await asyncio.gather(*(_row(sess, comp, s, active, timeout_s) for s in seats or [])))


# ---------------------------------------------------------------- the offline word

def offline_body(comp: dict, err) -> dict:
    """What the relay answers for a seat it can't reach: words, not an exception."""
    name = comp.get("seat_name") or "the seat"
    return {"error": "seat_offline", "seat": name, "seat_id": comp.get("active_seat"),
            "message": f"{name} is offline", "detail": repr(err)[:200]}
