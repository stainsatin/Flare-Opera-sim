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

    def test_committed_topology_is_valid(self):
        topology = ROOT / "topologies/opera_16tor_4host_15us.txt"
        GENERATOR.validate_topology(topology)
        first_line = topology.read_text(encoding="ascii").splitlines()[0]
        self.assertEqual(first_line, "64 4 4 16")


if __name__ == "__main__":
    unittest.main()
