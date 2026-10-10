"""Voice in the field UI (2026-10-10): the STT door's pure parts."""
import unittest

from ai_terminal import stt as S


class TestStt(unittest.TestCase):
    def test_only_wav_goes_through(self):
        self.assertTrue(S.looks_like_wav(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 40))
        self.assertFalse(S.looks_like_wav(b"\x1aE\xdf\xa3webm" + b"\x00" * 60))
        self.assertFalse(S.looks_like_wav(b"RIFF"))

    def test_the_upload_is_the_whisper_server_form(self):
        body, ctype = S.multipart(b"WAVDATA", hint="Argus")
        b = ctype.split("boundary=")[1]
        self.assertIn(b'name="file"; filename="u.wav"', body)
        self.assertIn(b"WAVDATA", body)
        self.assertIn(b'name="response_format"\r\n\r\njson', body)
        self.assertTrue(body.endswith(f"--{b}--\r\n".encode()))

    def test_non_speech_tags_are_dropped(self):
        self.assertEqual(S.clean_text(" [BLANK_AUDIO] turn on (music) the lights "), "turn on the lights")
