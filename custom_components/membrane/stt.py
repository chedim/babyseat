"""Voice in the field UI (ported from the boat's ai_terminal/stt.py): the
browser records, HA forwards to a whisper-server when one is set in the
integration's options (else the page listens itself, in the browser).

POST /api/membrane/stt  (HA auth; body: audio/wav, 16 kHz mono, <= 8 min)
  -> the whisper-server on the brain phone (the one calld uses), multipart
  -> {"text": "..."}

The audio stays on the LAN: browser -> HA -> the ThinkPhone. Nothing is
stored; the transcript goes back to the browser, which puts it in the chat.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .relay import component_of

_LOGGER = logging.getLogger(__name__)

MAX_BYTES = 15_400_000         # 8 min of 16 kHz s16 mono + header (HA itself refuses > 16 MiB)
STT_TIMEOUT_S = 180.0
HINT = "membrane, Home Assistant."


def multipart(wav: bytes, hint: str = HINT) -> tuple[bytes, str]:
    b = "aiterm" + uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"u.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav + \
           (f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson"
            f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\n{hint}"
            f"\r\n--{b}--\r\n").encode()
    return body, f"multipart/form-data; boundary={b}"


# what whisper says to silence and noise when it is the WHOLE transcript
SILENCE_PHRASES = {"you", "thank you", "thanks for watching", "thank you for watching", "bye"}


def clean_text(text: str) -> str:
    """whisper's non-speech tags ([BLANK_AUDIO], (music)) out, and its
    phrases-from-silence ('You', 'Thank you.') when they are all it heard."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", str(text or ""))
    text = " ".join(text.split())
    # a short subtitle-style line ("- Anywhere, guys.") is words made out of noise
    # (2026-10-11: "not even close to what I said"); a long one keeps its words
    m = re.match(r"^[-\u2013\u2014]\s*(.*)$", text)
    if m:
        if len(m.group(1).split()) <= 4:
            return ""
        text = m.group(1)
    if re.sub(r"[^a-z ]", "", text.lower()).strip() in SILENCE_PHRASES:
        return ""
    return text


def looks_like_wav(data: bytes) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


class SttView(HomeAssistantView):
    url = "/api/membrane/stt"
    name = "api:membrane:stt"
    requires_auth = True

    async def post(self, request):
        comp = component_of(request)
        url = comp.get("stt_url") or ""
        if not url:
            return web.json_response({"error": "speech recognition is not configured"}, status=503)
        data = await request.content.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return web.json_response({"error": "too long (8 minutes at most)"}, status=413)
        if not looks_like_wav(data):
            return web.json_response({"error": "expected a WAV recording"}, status=400)
        body, ctype = multipart(data, ", ".join([comp.get("name") or "Bob"] + list(comp.get("aliases") or [])) + ", Home Assistant.")
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, data=body, headers={"Content-Type": ctype},
                                     timeout=aiohttp.ClientTimeout(total=STT_TIMEOUT_S)) as up:
                    raw = await up.read()
                    if up.status != 200:
                        return web.json_response({"error": f"speech service {up.status}"}, status=502)
        except Exception as e:                          # noqa: BLE001
            _LOGGER.warning("membrane: stt unreachable: %r", e)
            return web.json_response({"error": "the speech service is not answering"}, status=502)
        try:
            text = json.loads(raw).get("text", "")
        except ValueError:
            text = ""
        return web.json_response({"text": clean_text(text)})
