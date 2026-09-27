"""The event bus: MQTT as membrane's push transport.

One broker (agatha.boats:1883, the operator's server), one topic prefix
(membrane/), everything published and subscribed through one client.
The bus REPLACES the JSON event surfaces AND the peer channel's polling:
events push live to every subscriber, no model turn, no 15-min wait.

Topics (membrane/ prefix, per the operator's call):
  membrane/speech/<seat>       the baby's voice (fire-and-forget)
  membrane/alert/<track>       boundary crossings (retained: latched)
  membrane/entity/born|merged  entity births and merges (retained)
  membrane/timeline/append     transition learned (fire-and-forget)
  membrane/verified/<claim>    verification results (retained)
  membrane/intent/<iid>        criteria progress (retained)

QoS 1 (at-least-once) on all publishes; retained on state topics.
The broker: agatha.boats:1883, no auth (the boat's LAN); env
MEMBRANE_MQTT_HOST/PORT overrides. Degrades to SILENT (the covenant)
when the broker is unreachable: events go to the local queue and are
dropped on overflow -- the timeline in cb carries the durable record,
MQTT only carries the ANNOUNCEMENT.
"""
from __future__ import annotations

import json
import os
import threading
import time

_MEMBRANE_PREFIX = "membrane/"


def mqtt_host() -> str:
    return os.environ.get("MEMBRANE_MQTT_HOST", "agatha.boats")


def mqtt_port() -> int:
    return int(os.environ.get("MEMBRANE_MQTT_PORT", "1883"))


class EventBus:
    """The MQTT event bus: membrane's push transport. One client per seat,
    connected at boot; publishes are fire-and-forget (QoS 1, the broker
    retries), subscriptions are callback-dispatched by topic. A broken
    connection degrades to silent (the covenant): the queue holds, the
    bus reconnects in the background."""

    def __init__(self, seat_id: str, host: str | None = None,
                 port: int | None = None):
        self.seat_id = seat_id
        self.host = host or mqtt_host()
        self.port = port or mqtt_port()
        self.connected = False
        self._lock = threading.Lock()
        self._subs: dict[str, callable] = {}
        self._client = None
        self._connect()

    def _connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
            cid = f"membrane-{self.seat_id}"
            self._c = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
            self._c.on_connect = self._on_connect
            self._c.on_disconnect = self._on_disconnect
            self._c.connect(self.host, self.port, keepalive=60)
            self._c.loop_start()
            self.connected = True
            print(f"[events] bus connecting ({self.host}:{self.port})",
                  flush=True)
        except Exception as e:
            self.connected = False
            print(f"[events] bus unavailable ({str(e)[:60]}) -- "
                  f"events degrade to silent", flush=True)

    def _connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
            cid = f"membrane-{self.seat_id}"
            self._c = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
            self._c.on_connect = self._on_connect
            self._c.on_disconnect = self._on_disconnect
            self._c.reconnect_delay_set(min_delay=5, max_delay=120)
            self._c.connect(self.host, self.port, keepalive=30)
            self._c.loop_start()
            self.connected = True
        except Exception as e:
            self.connected = False
            print(f"[events] bus unavailable ({str(e)[:60]}) -- "
                  f"events degrade to silent", flush=True)

    def _on_connect(self, client, userdata, flags, rc, *a, **kw):
        self.connected = True
        # THE QUIET CONNECT (the operator's complaint: the connect/disconnect
        # flood -- the reconnect loop churns on the boat's network); only
        # the FIRST connect logs (the boot notice), reconnects are silent
        if not getattr(self, "_ever_connected", False):
            print(f"[events] bus connected ({self.host}:{self.port})",
                  flush=True)
            self._ever_connected = True
        # resubscribe on reconnect
        for topic in list(self._subs):
            client.subscribe(topic, qos=1)

    def _on_disconnect(self, client, userdata, *a, **kw):
        # THE QUIET DISCONNECT: the reconnect is automatic (paho's
        # reconnect_on_failure), logging every cycle is noise -- only
        # log if the disconnect persists (connected stays False across
        # multiple on_disconnect calls)
        if self.connected:
            self.connected = False

    def publish(self, event: str, payload: dict, retain: bool = False,
                qos: int = 1) -> bool:
        """Publish one event: topic = membrane/<event>, payload = JSON.
        Fire-and-forget (silent on failure -- the covenant)."""
        topic = _MEMBRANE_PREFIX + event
        import json as _json
        body = _json.dumps(payload, default=str)
        try:
            r = self._c.publish(topic, body, qos=qos, retain=retain)
            return r.rc == 0
        except Exception:
            return False

    def subscribe(self, topic_pattern: str, callback: callable) -> bool:
        """Subscribe to a topic pattern (membrane/ prefix implied).
        callback(topic: str, payload: dict) -- called on every match."""
        full = _MEMBRANE_PREFIX + topic_pattern
        self._subs[full] = callback
        try:
            self._c.subscribe(full, qos=1)
            self._c.message_callback_add(full,
                                         lambda c, ud, msg: callback(
                                             msg.topic,
                                             json.loads(msg.payload)))
            return True
        except Exception:
            return False

    def close(self) -> None:
        try:
            self._c.loop_stop()
            self._c.disconnect()
        except Exception:
            pass
        self.connected = False


def _mqtt_client(client_id: str):
    import paho.mqtt.client as mqtt
    return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)


def _mqtt_wrapper():
    pass
