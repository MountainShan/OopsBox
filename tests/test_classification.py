import unittest

from dashboard.classification import (
    DEFAULT_CLASSIFICATION,
    VALID_CLASSIFICATIONS,
    agent_access,
    is_valid,
)


class TestClassificationValues(unittest.TestCase):
    def test_default_is_personal(self):
        self.assertEqual(DEFAULT_CLASSIFICATION, "personal")

    def test_exactly_three_valid_values(self):
        self.assertEqual(
            set(VALID_CLASSIFICATIONS),
            {"personal", "work", "work-restricted"},
        )

    def test_is_valid_accepts_known_values(self):
        for value in ("personal", "work", "work-restricted"):
            self.assertTrue(is_valid(value), value)

    def test_is_valid_rejects_unknown_and_miscased(self):
        for value in ("Personal", "WORK", "", "secret", "work_restricted"):
            self.assertFalse(is_valid(value), value)


class TestAgentGates(unittest.TestCase):
    def test_personal_hides_kiro_without_blocking_it(self):
        access = agent_access("personal")
        self.assertEqual(access["kiro"], "hidden")
        self.assertEqual(access["claude"], "available")
        self.assertEqual(access["codex"], "available")

    def test_work_allows_all_three(self):
        access = agent_access("work")
        self.assertEqual(access["claude"], "available")
        self.assertEqual(access["codex"], "available")
        self.assertEqual(access["kiro"], "available")

    def test_work_restricted_hard_blocks_cloud_agents(self):
        access = agent_access("work-restricted")
        self.assertEqual(access["claude"], "blocked")
        self.assertEqual(access["codex"], "blocked")
        self.assertEqual(access["kiro"], "available")

    def test_unknown_classification_falls_back_to_most_restrictive(self):
        access = agent_access("bogus")
        self.assertEqual(access["claude"], "blocked")
        self.assertEqual(access["codex"], "blocked")
        self.assertEqual(access["kiro"], "hidden")

    def test_fallback_is_never_looser_than_any_named_classification(self):
        rank = {"blocked": 2, "hidden": 1, "available": 0}
        fallback = agent_access("bogus")
        for name in VALID_CLASSIFICATIONS:
            for agent, state in agent_access(name).items():
                self.assertGreaterEqual(
                    rank[fallback[agent]],
                    rank[state],
                    f"fallback for {agent} is looser than {name}",
                )

    def test_returned_mapping_is_a_copy(self):
        first = agent_access("personal")
        first["kiro"] = "available"
        self.assertEqual(agent_access("personal")["kiro"], "hidden")


class TestFalsyClassificationDoesNotOpenTheGate(unittest.TestCase):
    """A present-but-falsy classification must not be promoted to `personal`.

    _classification_of() in dashboard/routers/projects.py passes an empty
    string through unchanged for a present-but-empty value, and coerces a
    present `None` to "" as well. Either way agent_access() must land on
    its strictest fallback, not on the permissive `personal` table.
    """

    def test_empty_string_and_none_coerced_block_cloud_agents(self):
        for classification in ("", None):  # "" is what a present `None`
            # value is coerced to by _classification_of() before it ever
            # reaches agent_access().
            with self.subTest(classification=classification):
                access = agent_access(classification)
                self.assertEqual(access["claude"], "blocked")
                self.assertEqual(access["codex"], "blocked")


if __name__ == "__main__":
    unittest.main()
