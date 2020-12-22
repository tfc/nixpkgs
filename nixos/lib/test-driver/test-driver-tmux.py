#! /somewhere/python3
from typing import Tuple, Any, Callable, Dict, Iterator, Optional, List
import argparse
import time
import os

import machine
import driver
import libtmux

class MachineTmux(machine.Machine):
    def __init__(self, args: Dict[str, Any]) -> None:
        machine.Machine.__init__(self, args)

        server = libtmux.Server(socket_path=os.environ["TMUX_SOCKET"])
        self.tmux_session = server.list_sessions()[0]

        self.log_file_name = os.path.join(self.tmp_dir, f"vm-log-{self.name}")
        self.log_file = open(self.log_file_name, "w")
        
        # The tmux window associated to this machine. This window
        # initally contains a pane with the log and another pane with
        # a terminal.
        self.tmux_window: Any = None

    def start(self):
        if self.tmux_window == None:
            self.tmux_window = self.tmux_session.new_window(
                window_name=f"{self.name}",
                attach=False,
                window_shell=f"tail -f {self.log_file_name}",
            )
            pane = self.tmux_window.split_window(
                attach=True, shell="tty; sleep 10d", percent=70
            )
            self._tty = pane.capture_pane()[0]
        
        machine.Machine.start(self)

    def tty(self):
        return self._tty

    def log_line(self, line):
        self.log_file.write("{}\n".format(line))
        self.log_file.flush()

    def clean_up(self):
        if self.tmux_window is not None:
            self.tmux_session.kill_window(target_window=self.tmux_window.name)


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument(
        "-K",
        "--keep-vm-state",
        help="re-use a VM state coming from a previous run",
        action="store_true",
    )
    (cli_args, vm_scripts) = arg_parser.parse_known_args()

    log = driver.Logger()

    def configure_python_repl(python_repl):
        # tmux doesn't support 256 colors by default and we don't want
        # to force it to 256 colors mode (in order to support non 256
        # terminals)
        python_repl.color_depth = "DEPTH_4_BIT"

    driver = driver.Driver(
        MachineTmux,
        log,
        vm_scripts,
        cli_args.keep_vm_state,
        configure_python_repl=configure_python_repl)

    driver.export_symbols()

    print("the list of machines:")
    for m in driver.machines:
        print(f"  {m.name}")

    tic = time.time()
    driver.run_tests()
    toc = time.time()
    print("test script finished in {:.2f}s".format(toc - tic))
