"""The overview's agent usage: each agent's /usage reduced to limit bars."""
import unittest

from dashboard import usage

CLAUDE = {"subscription_type": "max",
          "limits": [{"kind": "session", "percent": 3, "resets_at": "2026-10-25T15:29:59+00:00"},
                     {"kind": "weekly_all", "percent": 8, "resets_at": "2026-10-26T17:59:59+00:00"},
                     {"kind": "weekly_scoped", "percent": 0, "resets_at": None,
                      "scope": {"model": {"display_name": "Fable"}}}],
          "extra_usage": {"is_enabled": False}}
CODEX = {"rate_limits": {"primary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 1793530828},
                         "secondary": None, "credits": {"unlimited": False, "balance": "4968.99"},
                         "planType": "prolite"},
         "daily": [{"startDate": "2026-10-24", "tokens": 56336020}]}
KIRO = {"planName": "KIRO PRO", "billingCycleReset": "2026-11-01",
        "usageBreakdowns": [{"displayName": "Credits", "used": 3191.05, "limit": 1000.0, "percentage": 319,
                             "hasLimit": True, "currentOverages": 2191.05, "overageCharges": 87.64, "currency": "USD"}]}


class TestNormalize(unittest.TestCase):
    def test_claude(self):
        u = usage.from_claude(CLAUDE)
        self.assertEqual(u["plan"], "max")
        self.assertEqual([(l["label"], l["percent"]) for l in u["limits"]],
                         [("5h", 3), ("Weekly (all models)", 8), ("Weekly · Fable", 0)])

    def test_claude_without_limits_list(self):
        u = usage.from_claude({"rate_limits": {"five_hour": {"utilization": 40, "resets_at": "x"}}})
        self.assertEqual(u["limits"], [{"label": "5h", "percent": 40, "resets_at": "x"}])

    def test_codex(self):
        u = usage.from_codex(CODEX)
        self.assertEqual(u["plan"], "prolite")
        self.assertEqual(u["limits"][0]["label"], "Weekly")
        self.assertEqual(u["limits"][0]["percent"], 12)
        self.assertTrue(u["limits"][0]["resets_at"].startswith("2026-"))
        self.assertIn("Credits balance 4,969", u["notes"])

    def test_kiro_over_limit(self):
        u = usage.from_kiro(KIRO)
        self.assertEqual(u["limits"][0]["percent"], 319)
        self.assertEqual(u["limits"][0]["detail"], "3,191 / 1,000")
        self.assertTrue(any("87.64" in n for n in u["notes"]))

    def test_empty_answers_do_not_raise(self):
        for f in usage.NORMALIZE.values():
            self.assertEqual(f({})["limits"], [])


class FakeSession:
    def __init__(self, data=None, fail=False):
        self.data, self.fail, self.calls = data, fail, 0

    async def usage_data(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.data


class FakeManager:
    def __init__(self, sessions):
        self.sessions = sessions

    def any_alive(self, agent):
        return self.sessions.get(agent)

    def projects_running(self, agent):
        return ["p"] if agent in self.sessions else []


class TestAgentUsage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        usage._cache.clear()

    async def test_states_and_cache(self):
        claude = FakeSession(CLAUDE)
        m = FakeManager({"claude": claude, "codex": FakeSession(fail=True)})
        r = await usage.agent_usage(m, ["claude", "codex", "kiro"])
        self.assertEqual(r["claude"]["state"], "ok")
        self.assertEqual(r["codex"]["state"], "error")
        self.assertEqual(r["kiro"], {"state": "stopped", "running_in": []})
        await usage.agent_usage(m, ["claude"])
        self.assertEqual(claude.calls, 1)  # kept for a minute
        # claude stops: its last answer is still shown, marked old
        usage._cache["claude"]["at"] -= usage.TTL
        r = await usage.agent_usage(FakeManager({}), ["claude"])
        self.assertEqual(r["claude"]["state"], "stale")
        self.assertEqual(r["claude"]["plan"], "max")


if __name__ == "__main__":
    unittest.main()
