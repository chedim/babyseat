"""HOME ASSISTANT, MAPPED ONTO THE GRAPH (2026-10-30, the operator: "automatic
homeassistant entity, services and methods mapping onto membrane graph ... and
auto-integrate mqtt with the field if its configured in hass"). Home Assistant
pushes its catalog (entities + services); the seat builds one class per domain
(ha:light ...) under the root class `ha`, one live entity per HA entity
(ha_entity set, so HA's state pushes land on it), and each service as a method
on its domain class. MQTT rides HA's own client: an entity marked with
mqtt_topic is subscribed by HA, and the messages land on it."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import ha_bridge as HB  # noqa: E402
from membrane import night as N  # noqa: E402
import test_cliffhanger as C  # noqa: E402

CATALOG = {
    "entities": [{"entity_id": "light.port_strip", "name": "Port strip"},
                 {"entity_id": "light.aft_cabin", "name": "Aft cabin"},
                 {"entity_id": "switch.fan", "name": "Fan"}],
    "services": {"light": {"turn_on": {"target": True, "fields": ["brightness_pct"]},
                           "turn_off": {"target": True, "fields": []}},
                 "switch": {"toggle": {"target": True, "fields": []}},
                 "mqtt": {"publish": {"target": False, "fields": ["topic", "payload"]}},
                 "homeassistant": {"restart": {"target": False, "fields": []}}},
    "mqtt": True,
}


def _srv():
    srv, f, est, *_ = C._world()
    return srv, est


def _get(est, name):
    return est._resolve_spec(name)


class TestImport(unittest.TestCase):
    def test_domains_become_classes_and_entities_stay_live(self):
        srv, est = _srv()
        r = HB.import_catalog(srv, CATALOG)
        self.assertTrue(r["ok"], r)
        light = _get(est, "ha:light")
        self.assertEqual(light.properties["ha_domain"], "light")
        root = _get(est, "ha")
        self.assertIn(root.eid, light.properties["parents"])
        port = est.docs[HB.entity_eid("light.port_strip")]
        self.assertEqual(port.properties["ha_entity"], "light.port_strip")
        self.assertIn(light.eid, port.properties["parents"])
        self.assertIn("Port strip", port.properties.get("summary", ""))
        self.assertIn("light.port_strip", HB.subscriptions(srv))           # HA now pushes its state
        # a domain with only entity-less services (mqtt) still gets its class
        self.assertIsNotNone(_get(est, "ha:mqtt"))

    def test_services_are_methods_on_the_domain_class(self):
        srv, est = _srv()
        HB.import_catalog(srv, CATALOG)
        port = HB.entity_eid("light.port_strip")
        r = N.method_inspect(est, port, "turn_on")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["via"], _get(est, "ha:light").eid)
        self.assertTrue(N.method_inspect(est, port, "turn_on_with")["ok"])    # the data-carrying form
        self.assertFalse(N.method_inspect(est, port, "toggle").get("found"))     # a switch service, not a light's
        # the generic step the composites call binds the entity and the domain
        r = N.method_inspect(est, port, "service", {"service": "turn_on", "data": '{"brightness_pct": 40}'})
        self.assertIn("light.port_strip", r.get("would_run", "") or str(r), r)
        self.assertIn("/api/services/", str(r))
        # an entity-less service lives on its class and posts no entity_id
        r = N.method_inspect(est, _get(est, "ha:mqtt").eid, "domain_service",
                             {"service": "publish", "data": '{"topic": "a/b", "payload": "1"}'})
        self.assertTrue(r["ok"], r)
        self.assertNotIn("ha_entity", str(r.get("would_run", "")))

    def test_the_same_catalog_twice_is_a_no_op(self):
        srv, est = _srv()
        HB.import_catalog(srv, CATALOG)
        n = len(est.docs)
        r = HB.import_catalog(srv, CATALOG)
        self.assertTrue(r.get("unchanged"), r)
        self.assertEqual(len(est.docs), n)

    def test_an_entity_keeps_its_own_parents(self):
        srv, est = _srv()
        HB.import_catalog(srv, CATALOG)
        port = est.docs[HB.entity_eid("light.port_strip")]
        port.properties["parents"] = port.properties["parents"] + ["membrane::Component:boat-lights"]
        cat = dict(CATALOG, entities=CATALOG["entities"] + [{"entity_id": "light.new", "name": "New"}])
        HB.import_catalog(srv, cat)
        self.assertIn("membrane::Component:boat-lights", port.properties["parents"])


class _FakeHA(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _FakeHA.seen.append((self.path, self.headers.get("Authorization"), json.loads(body or b"{}")))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *a):
        pass


class TestTheCallReachesHomeAssistant(unittest.TestCase):
    """The composite runs for real against a fake HA (HOME is a temp dir, so the
    operator's own hass.env can never point it at the real house)."""

    def test_turn_on_with_and_publish_post_the_right_service(self):
        httpd = HTTPServer(("127.0.0.1", 0), _FakeHA)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        env = {"HOME": tempfile.mkdtemp(), "HASS_URL": f"http://127.0.0.1:{httpd.server_port}", "HASS_TOKEN": "tok"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            srv, f, est, *_ = C._world()
            HB.import_catalog(srv, CATALOG)
            _FakeHA.seen.clear()
            r = N.method_call(srv, est, f, None, HB.entity_eid("light.port_strip"), "turn_on_with",
                              {"data": '{"brightness_pct": 40}'})
            self.assertEqual(r.get("rc"), 0, r)
            r = N.method_call(srv, est, f, None, est._resolve_spec("ha:mqtt").eid, "publish_with",
                              {"data": '{"topic": "boat/x", "payload": "1"}'})
            self.assertEqual(r.get("rc"), 0, r)
            r = N.method_call(srv, est, f, None, HB.entity_eid("switch.fan"), "toggle")
            self.assertEqual(r.get("rc"), 0, r)
        finally:
            httpd.shutdown()
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(_FakeHA.seen, [
            ("/api/services/light/turn_on", "Bearer tok", {"brightness_pct": 40, "entity_id": "light.port_strip"}),
            ("/api/services/mqtt/publish", "Bearer tok", {"topic": "boat/x", "payload": "1"}),
            ("/api/services/switch/toggle", "Bearer tok", {"entity_id": "switch.fan"})])


class TestMqtt(unittest.TestCase):
    def test_marked_topics_are_subscribed_and_messages_land(self):
        srv, est = _srv()
        dock = C._add(est, "dock power", mqtt_topic="boat/dock/+/power")
        temp = C._add(est, "cabin temp", mqtt_topic="boat/cabin/temp")
        self.assertEqual(HB.mqtt_topics(srv), ["boat/cabin/temp", "boat/dock/+/power"])
        r = HB.apply_mqtt(srv, [{"topic": "boat/dock/a/power", "payload": "230"},
                                {"topic": "boat/other", "payload": "x"}])
        self.assertEqual(r, {"updated": 1, "unknown": 1})
        self.assertEqual(dock.properties["mqtt_payload"], "230")
        self.assertEqual(dock.properties["mqtt_last_topic"], "boat/dock/a/power")
        self.assertNotIn("mqtt_payload", temp.properties)
        self.assertEqual(HB.apply_mqtt(srv, [{"topic": "boat/dock/a/power", "payload": "230"}])["updated"], 0)

    def test_wildcards(self):
        m = HB.topic_matches
        self.assertTrue(m("a/#", "a/b/c"))
        self.assertTrue(m("a/#", "a"))
        self.assertTrue(m("a/+/c", "a/b/c"))
        self.assertFalse(m("a/+/c", "a/b/d"))
        self.assertFalse(m("a/b", "a/b/c"))


if __name__ == "__main__":
    unittest.main()
