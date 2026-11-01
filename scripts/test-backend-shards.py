"""Contracts for backend shard coverage and duration merging (no database)."""

from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from backend_test_shards import merge_durations, verify_partition


class BackendShardTests(unittest.TestCase):
    def test_partition_requires_equal_multisets_and_no_overlap(self):
        full = Counter(["a", "b", "c"])
        verify_partition(full, [Counter(["a", "c"]), Counter(["b"])])
        for shards in (
            [Counter(["a"]), Counter(["b"])],
            [Counter(["a", "b"]), Counter(["b", "c"])],
            [Counter(["a", "b"]), Counter(["c", "extra"])],
        ):
            with self.subTest(shards=shards), self.assertRaises(ValueError):
                verify_partition(full, shards)

    def test_duplicate_full_collection_cannot_hide_overlap(self):
        with self.assertRaises(ValueError):
            verify_partition(Counter(["a", "a"]), [Counter(["a"]), Counter(["a"])])

    def test_merge_preserves_measured_durations_and_rejects_duplicate_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = [Path(directory) / name for name in ("one.json", "two.json")]
            first.write_text(json.dumps({"a": 1.25}))
            second.write_text(json.dumps({"b": 2.5}))
            self.assertEqual(merge_durations([first, second]), {"a": 1.25, "b": 2.5})
            second.write_text(json.dumps({"a": 3}))
            with self.assertRaises(ValueError):
                merge_durations([first, second])


if __name__ == "__main__":
    unittest.main()
