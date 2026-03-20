#!/usr/bin/env python3

import glob
import json
import os
import subprocess
import textwrap
from argparse import ArgumentParser
from collections import deque
from tempfile import TemporaryDirectory
from itertools import chain
from pathlib import Path, PurePath
from pprint import pformat
from typing import (
    TypeAlias,
    TypedDict,
    Iterable,
)
import logging

Glob: TypeAlias = str
PathString: TypeAlias = str


class Mount(TypedDict):
    host: PathString
    guest: PathString


class Pattern(TypedDict):
    onFeatures: list[str]
    paths: list[Glob | Mount]
    unsafeFollowSymlinks: bool


AllowedPatterns: TypeAlias = dict[str, Pattern]


parser = ArgumentParser("pre-build-hook")
parser.add_argument("derivation_path")
parser.add_argument("sandbox_path", nargs="?")
parser.add_argument("--patterns", type=Path, required=True)
parser.add_argument("--nix-exe", type=Path, required=True)
parser.add_argument(
    "--issue-command",
    choices=("always", "conditional", "never"),
    default="conditional",
    help="Whether to print extra-sandbox-paths",
)
parser.add_argument(
    "--issue-stop",
    choices=("always", "conditional", "never"),
    default="conditional",
    help="Whether to print the final empty line",
)
parser.add_argument("-v", "--verbose", action="count", default=0)


class TemporaryTree(TemporaryDirectory):
    def __init__(self, *ops):
        """
        >>> with TemporaryTree(
        ...     ["root", "mkdir"],
        ...     ["root/c", "->", "b"],
        ...     ["root/b", "->", "a"],
        ...     ["root/a", "->", "../root"],
        ... ) as tt:
        ...     print(tt.listdir())
        ...     print(tt.listdir("root"))
        ...     print(tt.listdir("root/c"))
        ['root']
        ['a', 'b', 'c']
        ['a', 'b', 'c']
        """
        self.ops = ops
        super().__init__()

    def listdir(self, subdir="") -> list[str]:
        return sorted(os.listdir(Path(self.name, subdir)))

    def subst(self, path) -> str:
        return str(path).replace(self.name, "${TMP}")

    def unsubst(self, path) -> str:
        return str(path).replace("${TMP}", self.name)

    def to_abs(self, path) -> Path:
        if os.path.isabs(path):
            return Path(ptah)
        return Path(self.name, path)

    def symlink_targets(self, path):
        return [self.subst(p) for p in symlink_targets(self.to_abs(path))]

    def __enter__(self, *args, **kwargs):
        root_str = super().__enter__()
        root = Path(root_str)
        for op in self.ops:
            target = None
            match op:
                case [loc, "->", target]:
                    loc = self.to_abs(loc)
                    os.makedirs(loc.parent, exist_ok=True)
                    loc.symlink_to(self.unsubst(target))
                case [loc, "mkdir"]:
                    os.makedirs(self.to_abs(loc), exist_ok=True)
                case str(loc):
                    loc = self.to_abs(loc)
                    os.makedirs(loc.parent, exist_ok=True)
                    loc.touch()
                case _:
                    raise ValueError()
        return self


def symlink_targets(p: Path) -> list[Path]:
    """Traverse a chain of symlinks to collect every intermediate path up to the final destination.

    ## "Chain" setup:
    >>> with TemporaryTree(
    ...     "a",
    ...     ["b", "->", "a"],
    ...     ["c", "->", "b"],
    ... ) as tt:
    ...     print(tt.symlink_targets("a"))
    ...     print(tt.symlink_targets("b"))
    ...     print(tt.symlink_targets("c"))
    []
    ['${TMP}/a']
    ['${TMP}/b', '${TMP}/a']

    ## "Jump-out" setup:
    >>> with TemporaryTree(
    ...     "c/d",
    ...     ["a/b", "->", "../c/d"],
    ... ) as tt:
    ...     print(tt.symlink_targets("a/b"))
    ['${TMP}/c/d']

    ## "Far-up" relative symlink:
    >>> depth = 15
    >>> path = "x/" * depth
    >>> inv_path = "../" * depth
    >>> with TemporaryTree(
    ...     "o/p",
    ...     ["a/b", "->", f"../{path}"],
    ...     [f"{path}/n", "->", f"{inv_path}/o/p"],
    ... ) as tt:
    ...     print(tt.symlink_targets("a/b"))
    ...     print(tt.symlink_targets(f"{path}/n"))
    ['${TMP}/x/x/x/x/x/x/x/x/x/x/x/x/x/x/x']
    ['${TMP}/o/p']
    """

    out = []
    while p.is_symlink():
        target = p.readlink()
        if target.is_absolute():
            p = target
        else:
            # we need to resolve paths before concatenation because of things like
            # $ ls -l /sys/dev/char/226:128/subsystem
            # ... /sys/dev/char/226:128/subsystem
            # -> ../../../../../../class/drm
            # see also test_path_discovery_resolve_rel_links
            #
            # Path(normpath(...)) needed to normalize `foo/../bar` to `bar`
            p = Path(os.path.normpath(p.parent.resolve() / target))

        if p in out:
            break
        out.append(p)

    return out


def symlink_closure(path: Path) -> list[Path]:
    """Traverses all higher paths towards the final path. Returns all symlink targets on the way."""
    path_gen = (
        p
        for p in list(reversed(path.parents)) + [path]
        if str(p) != path.anchor
    )

    return sum(map(symlink_targets, path_gen), [])


def get_required_system_features(parsed_drv: dict) -> list[str]:
    # Newer versions of Nix (since https://github.com/NixOS/nix/pull/13263) store structuredAttrs
    # in the derivation JSON output.
    if "structuredAttrs" in parsed_drv:
        return parsed_drv["structuredAttrs"].get("requiredSystemFeatures", [])

    # Older versions of Nix store structuredAttrs in the env as a JSON string.
    drv_env = parsed_drv.get("env", {})
    if "__json" in drv_env:
        return list(
            json.loads(drv_env["__json"]).get("requiredSystemFeatures", [])
        )

    # Without structuredAttrs, requiredSystemFeatures is a space-separated string in env.
    return drv_env.get("requiredSystemFeatures", "").split()


def validate_mounts(
    pattern: Pattern,
) -> list[tuple[PathString, PathString, bool]]:
    roots: list[tuple[PathString, PathString, bool]] = []
    for mount in pattern["paths"]:
        if isinstance(mount, PathString):
            matches = glob.glob(mount)
            assert matches, f"Specified host paths do not exist: {mount}"

            roots.extend(
                (m, m, pattern["unsafeFollowSymlinks"]) for m in matches
            )
        else:
            assert isinstance(mount, dict) and "host" in mount, mount
            assert Path(
                mount["host"]
            ).exists(), f"Specified host paths do not exist: {mount['host']}"
            roots.append(
                (
                    mount["guest"],
                    mount["host"],
                    pattern["unsafeFollowSymlinks"],
                )
            )

    return roots


def match_mounts(
    allowed_patterns: AllowedPatterns, required_features: list[str]
) -> Iterable[tuple[PathString, PathString, bool]]:
    """List (guest, host, followlinks) triplets corresponding to `required_features`.

    >>> with TemporaryTree(
    ...     ["chain/a", "mkdir"],
    ...     ["chain/b", "->", "a"],
    ...     ["chain/c", "->", "b"],
    ...     ["jump/c/d", "mkdir"],
    ...     ["jump/a/b", "->", "../c/d"],
    ...     ["glob_base/a", "mkdir"],
    ...     ["glob_base/c", "mkdir"],
    ... ) as tt:
    ...     def subst_results(results):
    ...         return sorted((tt.subst(x), tt.subst(y), follow)
    ...             for x, y, follow in results)
    ...
    ...     allowed_patterns = {
    ...         "a": {
    ...             "onFeatures": ["feat_a"],
    ...             "paths": [f"{tt.to_abs('glob_base')}/*"],
    ...             "unsafeFollowSymlinks": True,
    ...         }
    ...     }
    ...     results_globbing = subst_results(match_mounts(allowed_patterns, ["feat_a"]))
    ...
    ...     a1 = tt.to_abs("chain/a").as_posix()
    ...     a2 = tt.to_abs("chain/b").as_posix()
    ...     b1 = tt.to_abs("jump/a").as_posix()
    ...     b2 = tt.to_abs("jump/c").as_posix()
    ...     allowed_patterns = {
    ...         "a": {
    ...             "onFeatures": ["feat_a", "feat_a1"],
    ...             "paths": [a1, a2],
    ...             "unsafeFollowSymlinks": True,
    ...         },
    ...         "b": {
    ...             "onFeatures": ["feat_b", "feat_b2"],
    ...             "paths": [b1, b2],
    ...             "unsafeFollowSymlinks": True,
    ...         },
    ...     }
    ...     results_empty = subst_results(match_mounts(allowed_patterns, []))
    ...     results_a = subst_results(match_mounts(allowed_patterns, ["feat_a"]))
    ...     results_b = subst_results(match_mounts(allowed_patterns, ["feat_b2"]))
    >>> print(pformat(results_globbing))
    [('${TMP}/glob_base/a', '${TMP}/glob_base/a', True),
     ('${TMP}/glob_base/c', '${TMP}/glob_base/c', True)]
    >>> print(results_empty)
    []
    >>> print(pformat(results_a))
    [('${TMP}/chain/a', '${TMP}/chain/a', True),
     ('${TMP}/chain/b', '${TMP}/chain/b', True)]
    >>> print(pformat(results_b))
    [('${TMP}/jump/a', '${TMP}/jump/a', True),
     ('${TMP}/jump/c', '${TMP}/jump/c', True)]
    """
    patterns: list[Pattern] = [
        pattern
        for pattern in allowed_patterns.values()
        if any(
            feature in required_features for feature in pattern["onFeatures"]
        )
    ]

    return (mnt for pattern in patterns for mnt in validate_mounts(pattern))


def mounts_closure(
    inputs: Iterable[tuple[PathString, PathString, bool]],
) -> list[tuple[PathString, PathString]]:
    """TODO: Explain how this is more than map(symlink_targets).

    >>> depth = 15
    >>> far_up = "x/" * depth
    >>> far_up_inv = "../" * depth
    >>> with TemporaryTree(
    ...     ["chain/a", "mkdir"],
    ...     ["chain/b", "->", "a"],
    ...     ["chain/c", "->", "b"],
    ...     ["../c/d", "mkdir"],
    ...     ["jump/a/b", "->", "../c/d"],
    ...     ["far-up/a/b", "->", f"../{far_up}"],
    ...     [f"far-up/{far_up}/n", "->", f"{far_up_inv}/o/p"],
    ... ) as tt:
    ...     cc = tt.to_abs("chain/c").as_posix()
    ...     ja = tt.to_abs("jump/a").as_posix()
    ...     paths = mounts_closure([
    ...         ("/guest/chain/c", cc, True),
    ...         ("/guest/jump/a", ja, True),
    ...     ])
    ...     paths = sorted(paths)
    ...     paths = [(tt.subst(x), tt.subst(y)) for x, y in paths]
    ...     paths_chain_jump = paths
    ...
    ...     far_up_b = tt.to_abs("far-up/a/b").as_posix()
    ...     paths = mounts_closure([
    ...         ("/guest/a/b", far_up_b, True),
    ...     ])
    ...     paths = sorted(paths)
    ...     paths = [(tt.subst(x), tt.subst(y)) for x, y in paths]
    ...     paths_far_up = paths
    >>> print(pformat(paths_chain_jump))
    [('/guest/chain/a', '${TMP}/chain/a'),
     ('/guest/chain/b', '${TMP}/chain/b'),
     ('/guest/chain/c', '${TMP}/chain/c'),
     ('/guest/jump/a', '${TMP}/jump/a'),
     ('/guest/jump/c/d', '${TMP}/jump/c/d')]
    >>> print(pformat(paths_far_up))
    [('/guest/a/b', '${TMP}/far-up/a/b'),
     ('/guest/o/p', '${TMP}/far-up/o/p'),
     ('/guest/x/x/x/x/x/x/x/x/x/x/x/x/x/x/x',
      '${TMP}/x/x/x/x/x/x/x/x/x/x/x/x/x/x/x')]
    """
    queue: deque[tuple[PathString, PathString, bool]] = deque(inputs)
    unique_mounts: set[tuple[PathString, PathString]] = set()
    mounts: list[tuple[PathString, PathString]] = []

    while queue:
        guest_path_str, host_path_str, follow_symlinks = queue.popleft()
        if (guest_path_str, host_path_str) not in unique_mounts:
            mounts.append((guest_path_str, host_path_str))
            unique_mounts.add((guest_path_str, host_path_str))

        if not follow_symlinks:
            continue

        host_path = Path(host_path_str)
        if not (host_path.is_dir() or host_path.is_symlink()):
            continue

        paths = [host_path] + [
            child for child in host_path.iterdir() if host_path.is_dir()
        ]

        for child in paths:
            for parent in symlink_closure(child):
                parent_str = parent.absolute().as_posix()
                if all(
                    not parent.absolute().is_relative_to(existing_path)
                    for existing_path, _ in unique_mounts
                ):
                    queue.append((parent_str, parent_str, follow_symlinks))
    return mounts


def prune_mounts(
    inputs: list[tuple[PathString, PathString, bool]],
) -> list[tuple[PathString, PathString, bool]]:
    """Deduplicate mountable paths, discarding children of already-mounted parents.

    >>> with TemporaryTree(
    ...     "c/d",
    ...     ["a/b", "->", "../c/d"],
    ... ) as tt:
    ...     a, b, root = [tt.to_abs(x).as_posix() for x in ["a", "b", ""]]
    ...     print([
    ...         (tt.subst(x), tt.subst(y), follow)
    ...         for x, y, follow
    ...         in prune_mounts([(a, a, True), (b, b, True), (root, root, True)])])
    [('${TMP}', '${TMP}', True)]

    >>> print(prune_mounts([]))
    []
    """

    if len(inputs) < 2:
        return inputs

    sorted_inputs = sorted(inputs)
    pruned = [sorted_inputs[0]]

    last_kept = Path(pruned[0][0])
    for current in sorted_inputs[1:]:
        if not Path(current[0]).is_relative_to(last_kept):
            pruned.append(current)
            last_kept = Path(current[0])

    return pruned


def entrypoint():
    args = parser.parse_args()

    VERBOSITY_LEVELS = [logging.ERROR, logging.INFO, logging.DEBUG]

    level_index = min(args.verbose, len(VERBOSITY_LEVELS) - 1)
    logging.basicConfig(level=VERBOSITY_LEVELS[level_index])

    drv_path = args.derivation_path

    with open(args.patterns, "r") as f:
        allowed_patterns = json.load(f)

    if not Path(drv_path).exists():
        logging.error(
            f"{drv_path} doesn't exist."
            " Cf. https://github.com/NixOS/nix/issues/9272"
            " Exiting the hook",
        )

    proc = subprocess.run(
        [
            args.nix_exe,
            "show-derivation",
            drv_path,
        ],
        capture_output=True,
    )
    try:
        parsed_drv = json.loads(proc.stdout)

        # compabitility: https://github.com/NixOS/nix/pull/14770
        if "derivations" in parsed_drv:
            parsed_drv = parsed_drv["derivations"]
    except json.JSONDecodeError:
        logging.error(
            "Couldn't parse the output of"
            "`nix show-derivation`"
            f". Expected JSON, observed: {proc.stdout}",
        )
        logging.error(
            textwrap.indent(proc.stdout.decode("utf8"), prefix=" " * 4)
        )
        logging.info("Exiting the nix-required-binds hook")
        return
    [canon_drv_path] = parsed_drv.keys()

    known_features = set(
        chain.from_iterable(
            pattern["onFeatures"] for pattern in allowed_patterns.values()
        )
    )

    parsed_drv = parsed_drv[canon_drv_path]
    required_features = get_required_system_features(parsed_drv)
    required_features = list(
        filter(known_features.__contains__, required_features)
    )

    mounts = prune_mounts(
        mounts_closure(
            match_mounts(allowed_patterns, required_features)
        )
    )

    # the pre-build-hook command
    if args.issue_command == "always" or (
        args.issue_command == "conditional" and mounts
    ):
        print("extra-sandbox-paths")
        print_paths = True
    else:
        print_paths = False

    # arguments, one per line
    for guest_path_str, host_path_str in mounts if print_paths else []:
        print(f"{guest_path_str}={host_path_str}")

    # terminated by an empty line
    something_to_terminate = args.issue_stop == "conditional" and mounts
    if args.issue_stop == "always" or something_to_terminate:
        print()


if __name__ == "__main__":
    entrypoint()
