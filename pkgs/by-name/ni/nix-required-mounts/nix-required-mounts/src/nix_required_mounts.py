import glob
import json
import os
import subprocess
import textwrap
from argparse import ArgumentParser
from collections import deque
from itertools import chain
from pathlib import Path, PurePath
from typing import (
    TypeAlias,
    TypedDict,
    Iterable,
    NotRequired,
)
import logging

Glob: TypeAlias = str
PathString: TypeAlias = str


class Mount(TypedDict):
    host: PathString
    guest: PathString


class Pattern(TypedDict):
    onFeatures: list[str]
    paths: list[Glob]
    unsafeFollowSymlinks: bool
    storePaths: list[PathString]
    mountTranslations: NotRequired[list[Mount]]


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


def symlink_targets(p: Path) -> list[Path]:
    """Traverses a chain of symlinks to collect every intermediate path up to the final destination."""

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


def expand_globs(paths: list[PathString]) -> list[PathString]:
    return sum(map(glob.glob, paths), [])


def enumerate_patterns(
    allowed_patterns: AllowedPatterns, required_features: list[str]
) -> Iterable[tuple[PathString, PathString, bool]]:
    patterns: list[Pattern] = [
        pattern
        for pattern in allowed_patterns.values()
        if any(
            feature in required_features for feature in pattern["onFeatures"]
        )
    ]

    return (mnt for pattern in patterns for mnt in validate_mounts(pattern))


def discover_reachable_paths(
    inputs: Iterable[PathString], follow_symlinks: bool
) -> list[PathString]:
    queue: deque[PathString] = deque(inputs)
    unique_paths: set[PathString] = set()
    reachable_paths: list[PathString] = []

    while queue:
        path_str = queue.popleft()
        print(path_str)
        if path_str not in unique_paths:
            reachable_paths.append(path_str)
            unique_paths.add(path_str)

        if not follow_symlinks:
            continue

        path = Path(path_str)
        if not (path.is_dir() or path.is_symlink()):
            continue

        paths = [path]
        if path.is_dir():
            paths += [child for child in path.iterdir()]

        for child in paths:
            for parent in symlink_closure(child):
                parent_str = parent.absolute().as_posix()
                if all(
                    not parent.absolute().is_relative_to(existing_path)
                    for existing_path in unique_paths
                ):
                    queue.append(parent_str)
    return reachable_paths


def prune_paths(inputs: list[PathString]) -> list[PathString]:
    if len(inputs) < 2:
        return inputs

    sorted_inputs = sorted(inputs)
    pruned = [sorted_inputs[0]]

    last_kept = pruned[0]
    for current in sorted_inputs[1:]:
        if not Path(current).is_relative_to(last_kept):
            pruned.append(current)
            last_kept = Path(current)

    return pruned


def parse_derivation(derivation_path: PathString) -> dict:
    if not Path(derivation_path).exists():
        logging.error(
            f"{drv_path} doesn't exist."
            " Cf. https://github.com/NixOS/nix/issues/9272"
            " Exiting the hook",
        )

    proc = subprocess.run(
        [
            args.nix_exe,
            "show-derivation",
            derivation_path,
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

    parsed_drv = parsed_drv[canon_drv_path]


def entrypoint():
    args = parser.parse_args()

    VERBOSITY_LEVELS = [logging.ERROR, logging.INFO, logging.DEBUG]

    level_index = min(args.verbose, len(VERBOSITY_LEVELS) - 1)
    logging.basicConfig(level=VERBOSITY_LEVELS[level_index])

    with open(args.patterns, "r") as f:
        allowed_patterns = json.load(f)

    parsed_drv = parse_derivation(args.derivation_path)

    known_features = set(
        chain.from_iterable(
            pattern["onFeatures"] for pattern in allowed_patterns.values()
        )
    )

    required_features = get_required_system_features(parsed_drv)
    required_features = list(
        filter(known_features.__contains__, required_features)
    )

    mounts = prune_paths(
        discover_reachable_paths(
            enumerate_patterns(allowed_patterns, required_features)
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
