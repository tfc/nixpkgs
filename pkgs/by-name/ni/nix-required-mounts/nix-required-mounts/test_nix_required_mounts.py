import unittest
import tempfile
import shutil
from pathlib import Path
from nix_required_mounts import (
    symlink_targets,
    enumerate_patterns,
    prune_paths,
    discover_reachable_paths,
    symlink_closure,
)


class TestNixRequiredMountsMethods(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)

        # 1. Multi-link setup
        self.ml_a = self.create_path("multi-link/a")
        self.ml_b = self.create_symlink("multi-link/b", "a")
        self.ml_c = self.create_symlink("multi-link/c", "b")

        # 2. Jump-out setup
        self.jo_target = self.create_path("jump-out/c/d")
        self.jo_link = self.create_symlink("jump-out/a/b", "../c/d")

        # 3. Globbing setup
        self.glob_base = "globbing"
        self.glob_a = self.create_path(f"{self.glob_base}/a")
        self.glob_c = self.create_path(f"{self.glob_base}/c")

        # 4. far-up rel symlink
        self.create_path("far-up/a")
        self.create_path("far-up/c/d/e/f/g/h/i/j/k/l/m")
        self.far_up_target2 = self.create_path("far-up/o/p")
        self.far_up_root = self.create_symlink(
            "far-up/a/b", "../c/d/e/f/g/h/i/j/k/l/m"
        )
        self.far_up_target1 = self.create_symlink(
            "far-up/c/d/e/f/g/h/i/j/k/l/m/n",
            "../../../../../../../../../../../o/p",
        )

        # 5.
        # - /
        #   - foo
        #     - b -> /bar
        #   - bar
        #     - c -> /baz
        #   - baz
        #   - a -> /foo
        self.connor_foo = self.create_path("connor/foo")
        self.connor_bar = self.create_path("connor/bar")
        self.connor_baz = self.create_path("connor/baz")

        self.create_symlink("connor/a", "foo")
        self.create_symlink("connor/foo/b", "../bar")
        self.create_symlink("connor/bar/c", "../baz")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def create_path(self, path_str):
        """Helper to create directories within the tmp folder."""
        path = self.root / path_str
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_symlink(self, link_path_str, target_path_str):
        """Helper to create symlinks within the tmp folder."""
        link_path = self.root / link_path_str
        target_path = self.root / target_path_str
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(target_path_str)
        return link_path

    def test_symlink_targets_neighbors(self):
        self.assertEqual(symlink_targets(self.ml_a), [])
        self.assertEqual(symlink_targets(self.ml_b), [self.ml_a])
        self.assertEqual(symlink_targets(self.ml_c), [self.ml_b, self.ml_a])

    def test_symlink_targets_jump_out(self):
        self.assertEqual(symlink_targets(self.jo_link), [self.jo_target])

    def test_symlink_targets_connor(self):
        source = self.root / "connor/a/b/c"
        self.assertEqual(
            sorted(symlink_closure(source)),
            sorted([self.connor_foo, self.connor_bar, self.connor_baz]),
        )

    def test_pattern_extraction(self):
        a1 = str(self.ml_a)
        a2 = str(self.ml_b)
        b1 = str(self.root / "jump-out/a")
        b2 = str(self.root / "jump-out/c")

        allowed_patterns = {
            "a": {
                "onFeatures": ["a", "a1"],
                "paths": [a1, a2],
                "unsafeFollowSymlinks": True,
            },
            "b": {
                "onFeatures": ["b", "b2"],
                "paths": [b1, b2],
                "unsafeFollowSymlinks": True,
            },
        }

        self.assertEqual(list(enumerate_patterns(allowed_patterns, [])), [])
        self.assertEqual(
            list(enumerate_patterns(allowed_patterns, ["a"])),
            [(a1, a1, True), (a2, a2, True)],
        )
        self.assertEqual(
            list(enumerate_patterns(allowed_patterns, ["b"])),
            [(b1, b1, True), (b2, b2, True)],
        )

    def test_pattern_globbing(self):
        full_base_path = str(self.root / self.glob_base)

        allowed_patterns = {
            "a": {
                "onFeatures": ["a"],
                "paths": [f"{full_base_path}/*"],
                "unsafeFollowSymlinks": True,
            }
        }

        results = list(enumerate_patterns(allowed_patterns, ["a"]))

        expected = [
            (str(self.glob_a), str(self.glob_a), True),
            (str(self.glob_c), str(self.glob_c), True),
        ]
        self.assertEqual(sorted(results), sorted(expected))

    def test_pruning(self):
        root = str(self.root / "jump-out")
        a = str(self.root / "jump-out/a")
        b = str(self.root / "jump-out/b")

        self.assertEqual(prune_paths([]), [])

        self.assertEqual(
            prune_paths([(a, a, True), (b, b, True), (root, root, True)]),
            [(root, root, True)],
        )

    def test_path_discovery(self):
        ma = str(self.root / "multi-link/a")
        mb = str(self.root / "multi-link/b")
        mc = str(self.root / "multi-link/c")
        ja = str(self.root / "jump-out/a")
        j_target = str(self.root / "jump-out/c/d")
        self.maxDiff = None
        self.assertEqual(
            sorted(discover_reachable_paths([(mc, mc, True), (ja, ja, True)])),
            [(ja, ja), (j_target, j_target), (ma, ma), (mb, mb), (mc, mc)],
        )

    def test_path_discovery_resolve_rel_links(self):
        self.maxDiff = None
        root = str(self.far_up_root)
        t1 = str(self.far_up_target1.parent)
        t2 = str(self.far_up_target2)

        self.assertEqual(
            discover_reachable_paths([(root, root, True)]),
            [(root, root), (t1, t1), (t2, t2)],
        )


if __name__ == "__main__":
    unittest.main()
