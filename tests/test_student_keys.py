"""student_key formula tests — cross-check vectors with the ml-data pipeline.

Source of truth: ml-data/docs/M5_PREDICTION_BATCH.md §2
(mirrored in DB_CONTRACT.md v1.0 rev2 §9.1).
"""

import unittest

from learnguard_backend.services.student_keys import (
    TEST_VECTORS,
    keys_for_students,
    pseudonymous_key,
)


class PseudonymousKeyTests(unittest.TestCase):
    def test_cross_check_vectors_pass_absolutely(self) -> None:
        """AC1: both pipeline test vectors must pass."""
        self.assertGreaterEqual(len(TEST_VECTORS), 2)
        for code_module, code_presentation, id_student, expected in TEST_VECTORS:
            with self.subTest(module=code_module, presentation=code_presentation):
                self.assertEqual(
                    pseudonymous_key(code_module, code_presentation, id_student),
                    expected,
                )

    def test_vector_1(self) -> None:
        self.assertEqual(pseudonymous_key("AAA", "2013B", 1), "4c337e13fc7b6871")

    def test_vector_2(self) -> None:
        self.assertEqual(
            pseudonymous_key("AAA", "2013J", 11391), "7bd60aced1ccab8c"
        )

    def test_key_is_16_lowercase_hex(self) -> None:
        key = pseudonymous_key("BBB", "2014B", 12345)
        self.assertEqual(len(key), 16)
        self.assertRegex(key, r"^[0-9a-f]{16}$")

    def test_distinct_inputs_yield_distinct_keys(self) -> None:
        self.assertNotEqual(
            pseudonymous_key("AAA", "2013B", 1), pseudonymous_key("AAA", "2013B", 2)
        )
        self.assertNotEqual(
            pseudonymous_key("AAA", "2013B", 1), pseudonymous_key("AAA", "2013J", 1)
        )

    def test_batch_computation_matches_single(self) -> None:
        students = [("AAA", "2013B", 1), ("AAA", "2013J", 11391)]
        keys = keys_for_students(students)
        self.assertEqual(
            keys[("AAA", "2013B", 1)], pseudonymous_key("AAA", "2013B", 1)
        )
        self.assertEqual(
            keys[("AAA", "2013J", 11391)], pseudonymous_key("AAA", "2013J", 11391)
        )


if __name__ == "__main__":
    unittest.main()
