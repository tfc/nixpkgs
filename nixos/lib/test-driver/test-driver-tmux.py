#! /somewhere/python3
from typing import Tuple, Any, Callable, Dict, Iterator, Optional, List
import argparse
import time
import os
import tempfile

from machine import Machine
from driver import Driver, Logger
import libtmux

class MachineTmuxer:
    def __init__(self, name, log_directory) -> None:
        self.server = libtmux.Server(socket_path=os.environ["TMUX_SOCKET"])
        self.tmux_session = self.server.list_sessions()[0]
        self.name = name

        self.log_file_name = os.path.join(log_directory, f"vm-log-{name}")
        self.log_file = open(self.log_file_name, "w")

        self.tmux_window = None

    def create_window(self) -> None:
        if self.tmux_window is not None:
            return

        self.tmux_window = self.tmux_session.new_window(
            window_name=f"{self.name}",
            attach=False,
            window_shell=f"echo 'The machine {self.name} has not been started yet'; tail -f {self.log_file_name}",
        )
        pane = self.tmux_window.split_window(
            attach=True, shell="while true; do socat /tmp/lelelelelel -; done", percent=70
        )

    def log_line(self, line: str) -> None:
        self.create_window()
        self.log_file.write("{}\n".format(line))
        self.log_file.flush()

    def release(self) -> None:
        self.log_file.close()
        if self.tmux_window is not None:
            self.tmux_session.kill_window(target_window=self.tmux_window.name)

def main() -> None:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument(
        "-K",
        "--keep-vm-state",
        help="re-use a VM state coming from a previous run",
        action="store_true",
    )
    (cli_args, vm_scripts) = arg_parser.parse_known_args()

    log = Logger()

    def configure_python_repl(python_repl):
        # tmux doesn't support 256 colors by default and we don't want
        # to force it to 256 colors mode (in order to support non 256
        # terminals)
        python_repl.color_depth = "DEPTH_4_BIT"

    tmpdir = os.environ.get("TMPDIR", tempfile.gettempdir())
    logdir = os.path.join(tmpdir, "tmux-logs")
    os.makedirs(logdir, mode=0o700, exist_ok=True)
    tmuxers = {}

    def machine_config_modifier(args:  Dict[str, Any]) -> Dict[str, Any]:
        name = args["name"]
        tmuxer = MachineTmuxer(name, logdir)
        tmuxers[name] = tmuxer
        args["log_serial"] = lambda x: tmuxer.log_line(f"[{name} serial] {x}")
        args["log_machinestate"] = lambda x: tmuxer.log_line(f"[{name} machine-ctrl] {x}")
        args["tty_path"] = "/tmp/lelelelelel" #tmuxer.tty_path()
        return args

    try:
        driver = Driver(
            Machine,
            log,
            vm_scripts,
            cli_args.keep_vm_state,
            configure_python_repl=configure_python_repl,
            machine_config_modifier=machine_config_modifier)


        driver.export_symbols()

        print("the list of machines:")
        for m in driver.machines:
            print(f"  {m.name}")

        tic = time.time()
        driver.run_tests()
        toc = time.time()
        print("test script finished in {:.2f}s".format(toc - tic))
    finally:
        for tmuxer in tmuxers.values():
            tmuxer.release()


if __name__ == "__main__":
    main()
