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
    expand_globs,
)
import os
import pytest
from pathlib import Path


class TreeBuilder:
    """Helper to create files and symlinks from a simple dict."""

    def __init__(self, root: Path):
        self.root = root

    def build(self, structure: dict):
        for path_str, target in structure.items():
            path = self.root / path_str
            path.parent.mkdir(parents=True, exist_ok=True)

            if target == "file":
                path.touch()
            elif target == "dir":
                path.mkdir(parents=True, exist_ok=True)
            elif target.startswith("->"):
                link_to = target.replace("->", "").strip()
                path.symlink_to(link_to)
        return self.root


@pytest.fixture
def tree(tmp_path):
    # https://docs.pytest.org/en/stable/how-to/tmp_path.html
    return TreeBuilder(tmp_path)


def test_symlink_chain(tree):
    root = tree.build({"a": "file", "b": "-> a", "c": "-> b"})

    assert symlink_targets(root / "a") == []
    assert symlink_targets(root / "b") == [root / "a"]
    assert symlink_targets(root / "c") == [root / "b", root / "a"]


def test_far_up_relative_links(tree):
    depth = 15
    path = "x/" * depth
    root = tree.build(
        {
            "o/p": "file",
            "a/b": f"-> ../{path}",
            f"{path}/n": f"-> ../{'../' * depth}o/p",
        }
    )

    assert symlink_targets(root / "a/b") == [root / Path(path)]


def test_jump_outside_folder(tree):
    root = tree.build({"c/d": "file", "a/b": "-> ../c/d"})
    assert symlink_targets(root / "a/b") == [root / "c/d"]


def test_nested_symlink_closure(tree):
    root = tree.build(
        {
            "foo": "dir",
            "bar": "dir",
            "baz": "dir",
            "a": "-> foo",
            "foo/b": "-> ../bar",
            "bar/c": "-> ../baz",
        }
    )

    assert symlink_closure(root / "a/b/c"), [
        tree / "foo",
        tree / "bar",
        tree / "baz",
    ]


def test_pattern_extraction(tree):
    root = tree.build(
        {
            "a": "file",
            "b": "-> a",
            "c": "-> b",
            "d/e": "file",
            "f/g": "-> ../d/e",
        }
    )

    allowed_patterns = {
        "a": {
            "onFeatures": ["feature_a", "feature_a1"],
            "paths": [root / "a", root / "b"],
            "unsafeFollowSymlinks": True,
        },
        "b": {
            "onFeatures": ["feature_b", "feature_b2"],
            "paths": [root / "d", root / "f"],
            "unsafeFollowSymlinks": True,
        },
    }

    assert list(enumerate_patterns(allowed_patterns, [])) == []

    assert list(enumerate_patterns(allowed_patterns, ["feature_a"])) == [
        (root / "a", root / "a", True),
        (root / "b", root / "b", True),
    ]
    assert list(enumerate_patterns(allowed_patterns, ["feature_b"])) == [
        (root / "d", root / "d", True),
        (root / "f", root / "f", True),
    ]


def test_glob_expansion(tree):
    root = tree.build({"a": "file", "b": "-> a", "c": "-> b"})

    assert sorted(expand_globs([str(root / "*")])) == list(
        map(str, [root / "a", root / "b", root / "c"])
    )


def test_pruner(tree):
    root = tree.build({"c/d": "file", "a/b": "-> ../c/d"})

    assert prune_paths([]) == []

    assert prune_paths([(root / "a"), (root / "b"), root]) == [root]


def test_path_discovery(tree):
    root = tree.build(
        {
            "a": "file",
            "b": "-> a",
            "c": "-> b",
            "d/e": "file",
            "f/g": "-> ../d/e",
        }
    )

    ss = lambda unsorted_paths: sorted(map(str, unsorted_paths))

    assert ss(
        discover_reachable_paths(
            [root / "c", root / "f"], follow_symlinks=True
        )
    ) == ss([root / "a", root / "b", root / "c", root / "d/e", root / "f"])


def test_path_discovery_resolve_rel_links(tree):
    depth = 15
    path = "x/" * depth
    root = tree.build(
        {
            "o/p": "file",
            "a/b": f"-> ../{path}",
            f"{path}/n": f"-> ../{'../' * depth}o/p",
        }
    )

    assert discover_reachable_paths([root], follow_symlinks=True) == [root]


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


if __name__ == "__main__":
    unittest.main()
