import unittest
import tempfile
import shutil
from pathlib import Path
from nix_required_mounts import (
    symlink_targets,
    prune_paths,
    discover_reachable_paths,
    symlink_closure,
    expand_globs,
    Pattern,
    path_closure,
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

    a = {
        "onFeatures": ["feature_a", "feature_a1"],
        "paths": list(map(str, [root / "a", root / "b"])),
        "storePaths": [],
        "unsafeFollowSymlinks": True,
    }

    assert path_closure(a) == [
        (str(root / "a"), str(root / "a")),
        (str(root / "b"), str(root / "b")),
    ]

    b = {
        "onFeatures": ["feature_b", "feature_b2"],
        "paths": list(map(str, [root / "d", root / "f"])),
        "storePaths": [],
        "unsafeFollowSymlinks": True,
    }

    assert path_closure(b) == [
        (str(root / "d"), str(root / "d")),
        (str(root / "f"), str(root / "f")),
    ]

    b2 = b | {"mountTranslations": [{"host": str(root), "guest": "/usr/lib"}]}

    assert path_closure(b2) == [
        ("/usr/lib/d", str(root / "d")),
        ("/usr/lib/f", str(root / "f")),
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


if __name__ == "__main__":
    unittest.main()
