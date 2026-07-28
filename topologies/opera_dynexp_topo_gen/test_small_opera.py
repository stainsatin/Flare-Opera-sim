import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "small_opera_generator", HERE / "generate_small_opera.py"
)
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class SmallOperaTopologyTest(unittest.TestCase):
    def test_one_factorization_covers_every_pair(self):
        matchings = GENERATOR.one_factorization(16)
        self.assertEqual(len(matchings), 16)
        pairs = set()
        for matching in matchings:
            self.assertEqual(len(matching), 16)
            for source, destination in enumerate(matching):
                self.assertEqual(matching[destination], source)
                if source <= destination:
                    pairs.add((source, destination))
        self.assertEqual(len(pairs), 16 * 17 // 2)

    def test_24tor_rotors_reconfigure_one_at_a_time(self):
        schedule = GENERATOR.rotor_schedule(24, 6)
        self.assertTrue(
            all(
                sum(schedule[rotor][superslice] is None for rotor in range(6)) == 1
                for superslice in range(24)
            )
        )
        self.assertEqual(
            [sum(slot is None for slot in rotor) for rotor in schedule],
            [4] * 6,
        )

    def test_committed_topologies_are_valid(self):
        expected = {
            "opera_16tor_4host_15us.txt": (
                "64 4 4 16",
                "48 12880000 620000 1000000",
                8,
            ),
            "opera_16tor_4host_55us.txt": (
                "64 4 4 16",
                "48 53380000 620000 1000000",
                8,
            ),
            "opera_24tor_4host_55us.txt": (
                "96 4 6 24",
                "72 53380000 620000 1000000",
                4,
            ),
        }
        for filename, (header, timing, max_hops) in expected.items():
            with self.subTest(topology=filename):
                topology = ROOT / "topologies" / filename
                metrics = GENERATOR.validate_topology(topology)
                lines = topology.read_text(encoding="ascii").splitlines()
                self.assertEqual(lines[0], header)
                self.assertEqual(lines[1], timing)
                self.assertLessEqual(metrics["max_hops"], max_hops)

    def test_asymmetric_host_and_rotor_counts_are_supported(self):
        with self.assertRaisesRegex(ValueError, "specified together"):
            GENERATOR.generate_topology(
                output=HERE / "unused.txt",
                tors=24,
                downlinks=4,
            )


if __name__ == "__main__":
    unittest.main()
