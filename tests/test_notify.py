"""Notifications: subscriptions, usage alerts, and agents asking for the user."""
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from dashboard import notify
from tests.test_chat_drivers import Base, FakeAcp, pending_permission, wait_for


class Isolated(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self.p = unittest.mock.patch.multiple(
            notify, CONFIG=d, _key_file=d / "k.pem", _subs_file=d / "subs.json",
            _alerts_file=d / "alerts.json", _settings_file=d / "settings.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self._tmp.cleanup()


class TestSubscriptions(Isolated):
    def test_subscribe_replaces_same_endpoint(self):
        sub = {"endpoint": "https://push.example/1", "keys": {"p256dh": "a", "auth": "b"}}
        notify.subscribe(sub)
        notify.subscribe({**sub, "keys": {"p256dh": "c", "auth": "d"}})
        self.assertEqual(notify.subscriptions(), [{"endpoint": sub["endpoint"], "keys": {"p256dh": "c", "auth": "d"}}])
        notify.unsubscribe(sub["endpoint"])
        self.assertEqual(notify.subscriptions(), [])

    def test_signed_for_the_address_it_subscribed_on(self):
        keys = {"p256dh": "a", "auth": "b"}
        notify.subscribe({"endpoint": "https://push.example/1", "keys": keys, "origin": "https://box.example.ts.net:9444"})
        notify.subscribe({"endpoint": "https://push.example/2", "keys": keys, "origin": "http://box:9080"})
        a, b = notify.subscriptions()
        self.assertEqual(a["subject"], "https://box.example.ts.net")
        self.assertNotIn("subject", b)
        claims = []
        with unittest.mock.patch("pywebpush.webpush", side_effect=lambda **kw: claims.append(kw["vapid_claims"]["sub"])):
            notify._vapid()
            notify._send_one(a, "{}"); notify._send_one(b, "{}")
        self.assertEqual(claims, ["https://box.example.ts.net", notify.SUBJECT])

    def test_rejects_what_is_not_a_subscription(self):
        for bad in ({}, {"endpoint": "http://x", "keys": {}}, {"endpoint": "https://x"}, "x"):
            with self.assertRaises(ValueError):
                notify.subscribe(bad)

    def test_messages_are_signed_and_encrypted_for_the_browser(self):
        import base64, json, os
        import http_ece
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        priv = ec.generate_private_key(ec.SECP256R1())
        auth = os.urandom(16)
        pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        sub = {"endpoint": "https://push.example/x", "keys": {"p256dh": b64(pub), "auth": b64(auth)}}
        sent = {}

        class Resp:
            status_code = 201
            headers = {}
            text = ""
        def post(url, data=None, headers=None, **_):
            sent.update(url=url, data=data, headers=headers)
            return Resp()
        notify._vapid()
        with unittest.mock.patch("requests.post", side_effect=post):
            self.assertIsNone(notify._send_one(sub, json.dumps({"title": "t", "body": "hello"})))
        self.assertTrue(sent["headers"]["Authorization"].startswith("vapid t="))
        body = http_ece.decrypt(sent["data"], private_key=priv, auth_secret=auth, version="aes128gcm")
        self.assertEqual(json.loads(body)["body"], "hello")

    def test_public_key_is_stable(self):
        k = notify.public_key()
        self.assertEqual(len(k), 87)  # 65-byte uncompressed point, base64url
        self.assertEqual(notify.public_key(), k)


class TestUsageAlerts(Isolated):
    def test_each_limit_once_per_reset(self):
        agents = {"kiro": {"limits": [{"label": "Credits", "percent": 319, "resets_at": "2026-11-01"}]},
                  "codex": {"limits": [{"label": "Weekly", "percent": 10, "resets_at": "x"}]}}
        due = notify.usage_alerts(agents, threshold=90)
        self.assertEqual(len(due), 1)
        self.assertIn("kiro usage at 319%", due[0][0])
        self.assertEqual(notify.usage_alerts(agents, threshold=90), [])
        agents["kiro"]["limits"][0]["resets_at"] = "2026-12-01"  # a new period
        self.assertEqual(len(notify.usage_alerts(agents, threshold=90)), 1)

    def test_threshold_from_settings(self):
        self.assertEqual(notify.alert_pct(), notify.DEFAULT_ALERT_PCT)
        (notify._settings_file).write_text('{"usage_alert_pct": 75}')
        self.assertEqual(notify.alert_pct(), 75)


class TestAttention(Base):
    async def test_permission_calls_the_hook(self):
        s = self.make(FakeAcp)
        seen = []
        s.on_attention = lambda kind, text: seen.append(kind)
        await s.prompt("hi")
        await wait_for(lambda: pending_permission(s) is not None)
        self.assertEqual(seen, ["permission"])
        await s.stop()


if __name__ == "__main__":
    unittest.main()
