import unittest
import tempfile
import shutil
from pathlib import Path
from nix_required_mounts import (
    PathString,
    Pattern,
    discover_reachable_paths,
    expand_globs,
    path_closure,
    prune_paths,
    symlink_closure,
    symlink_targets,
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
    # the paths are kept on error for inspection
    return TreeBuilder(tmp_path)


def test_symlink_chain(tree):
    root = tree.build(
        {  # prevent the formatter
            "a": "file",  # from formatting
            "b": "-> a",  #
            "c": "-> b",  #
        }
    )

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
    root = tree.build(
        {  # prevent the formatter
            "c/d": "file",  # from formatting
            "a/b": "-> ../c/d",  #
        }
    )
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


def test_path_discovery_resolve_relative_links(tree):
    depth = 15
    path = "x/" * depth
    root = tree.build(
        {
            "o/p": "file",
            "a/b": f"-> ../{path}",
            f"{path}/n": f"-> {'../' * depth}o/p",
        }
    )

    assert discover_reachable_paths([root / "a"], follow_symlinks=True) == [
        str(x) for x in [root / "a", root / path, root / "o/p"]
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

    def pairs(paths: list[PathString]) -> list[tuple[str, str]]:
        return [(str(x), str(x)) for x in paths]

    a = {
        "onFeatures": ["feature_a", "feature_a1"],
        "paths": list(map(str, [root / "c"])),
        "storePaths": [],
        "unsafeFollowSymlinks": True,
    }

    assert path_closure(a) == pairs([root / "a", root / "b", root / "c"])

    assert path_closure(a | {"unsafeFollowSymlinks": False}) == pairs(
        [root / "c"]
    )

    b = {
        "onFeatures": ["feature_b", "feature_b2"],
        "paths": list(map(str, [root / "d", root / "f"])),
        "storePaths": [],
        "unsafeFollowSymlinks": True,
    }

    assert path_closure(b) == pairs(
        [
            root / "d",
            root / "f",
        ]
    )

    b2 = b | {"mountTranslations": [{"host": str(root), "guest": "/usr/lib"}]}

    assert path_closure(b2) == [
        ("/usr/lib/d", str(root / "d")),
        ("/usr/lib/f", str(root / "f")),
    ]


def test_glob_expansion(tree):
    root = tree.build(
        {
            "a": "file",  #
            "b": "-> a",  #
            "c": "-> b",  #
        }
    )

    assert sorted(expand_globs([str(root / "*")])) == [
        str(x) for x in [root / "a", root / "b", root / "c"]
    ]


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
    ) == ss(
        [
            root / "a",  # prevent formatting
            root / "b",
            root / "c",
            root / "d/e",
            root / "f",
        ]
    )


if __name__ == "__main__":
    unittest.main()
