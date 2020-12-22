from contextlib import contextmanager, _GeneratorContextManager
from queue import Queue, Empty
from typing import Tuple, Any, Callable, Dict, Iterator, Optional, List
from xml.sax.saxutils import XMLGenerator
import queue
import io
import _thread
import argparse
import atexit
import base64
import codecs
import os
import pathlib
import ptpython.repl
import pty
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
import os

import common
# For typing
import machine


def test_script() -> None:
    exec(os.environ["testScript"])


class Driver():
    def __init__(self, machine_class, log, vm_scripts, keep_vm_state,
                 configure_python_repl=id):
        """
        Args:
            - configure_python_repl: a function to configure the ptpython.repl
o        """
        self.log = log
        self.machine_class = machine_class
        self.vm_scripts = vm_scripts
        self.configure_python_repl = configure_python_repl
        
        vlan_nrs = list(dict.fromkeys(os.environ.get("VLANS", "").split()))
        vde_sockets = [self.create_vlan(v) for v in vlan_nrs]
        for nr, vde_socket, _, _ in vde_sockets:
            os.environ["QEMU_VDE_SOCKET_{}".format(nr)] = vde_socket

        self.machines = [
            self.create_machine({"startCommand": s, "keepVmState": keep_vm_state})
            for s in self.vm_scripts
        ]

        @atexit.register
        def clean_up() -> None:
            with self.log.nested("cleaning up"):
                for machine in self.machines:
                    machine.clean_up()
                    if machine.pid is None:
                        continue
                    log.log("killing {} (pid {})".format(machine.name, machine.pid))
                    machine.process.kill()
                for _, _, process, _ in vde_sockets:
                    process.terminate()
            log.close()

    @contextmanager
    def subtest(self, name: str) -> Iterator[None]:
        with self.log.nested(name):
            try:
                yield
                return True
            except Exception as e:
                self.log.log(f'Test "{name}" failed with error: "{e}"')
                raise e

        return False

    def export_symbols(self):
        global machines
        machines = self.machines
        machine_eval = [
            "global {0}; {0} = machines[{1}]".format(m.name, idx) for idx, m in enumerate(machines)
        ]

        exec("\n".join(machine_eval))

        global start_all
        start_all = self.start_all

        global subtest
        subtest = lambda name: self.subtest(name)

    def create_machine(self, args: Dict[str, Any]) -> machine.Machine:
        args["log"] = self.log
        args["redirectSerial"] = os.environ.get("USE_SERIAL", "0") == "1"
        return self.machine_class(args)

    def run_tests(self) -> None:
        tests = os.environ.get("tests", None)
        if tests is not None:
            with self.log.nested("running the VM test script"):
                try:
                    exec(tests, globals())
                except Exception as e:
                    common.eprint("error: ")
                    traceback.print_exc()
                    sys.exit(1)
        else:
            ptpython.repl.embed(
                locals(), globals(),
                configure=self.configure_python_repl)
        # TODO: Collect coverage data

        for machine in self.machines:
            if machine.is_up():
                machine.execute("sync")

    def start_all(self) -> None:
        with self.log.nested("starting all VMs"):
            for machine in self.machines:
                machine.start()

    def join_all(self) -> None:
        with self.log.nested("waiting for all VMs to finish"):
            for machine in self.machines:
                machine.wait_for_shutdown()

    def create_vlan(self, vlan_nr: str) -> Tuple[str, str, "subprocess.Popen[bytes]", Any]:
        self.log.log("starting VDE switch for network {}".format(vlan_nr))
        vde_socket = tempfile.mkdtemp(
            prefix="nixos-test-vde-", suffix="-vde{}.ctl".format(vlan_nr)
        )
        pty_master, pty_slave = pty.openpty()
        vde_process = subprocess.Popen(
            ["vde_switch", "-s", vde_socket, "--dirmode", "0700"],
            stdin=pty_slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        fd = os.fdopen(pty_master, "w")
        fd.write("version\n")
        # TODO: perl version checks if this can be read from
        # an if not, dies. we could hang here forever. Fix it.
        assert vde_process.stdout is not None
        vde_process.stdout.readline()
        if not os.path.exists(os.path.join(vde_socket, "ctl")):
            raise Exception("cannot start vde_switch")

        return (vlan_nr, vde_socket, vde_process, fd)


class Logger:
    def __init__(self) -> None:
        self.logfile = os.environ.get("LOGFILE", "/dev/null")
        self.logfile_handle = codecs.open(self.logfile, "wb")
        self.xml = XMLGenerator(self.logfile_handle, encoding="utf-8")
        self.queue: "Queue[Dict[str, str]]" = Queue()

        self.xml.startDocument()
        self.xml.startElement("logfile", attrs={})

    def close(self) -> None:
        self.xml.endElement("logfile")
        self.xml.endDocument()
        self.logfile_handle.close()

    def sanitise(self, message: str) -> str:
        return "".join(ch for ch in message if unicodedata.category(ch)[0] != "C")

    def maybe_prefix(self, message: str, attributes: Dict[str, str]) -> str:
        if "machine" in attributes:
            return "{}: {}".format(attributes["machine"], message)
        return message

    def log_line(self, message: str, attributes: Dict[str, str]) -> None:
        self.xml.startElement("line", attributes)
        self.xml.characters(message)
        self.xml.endElement("line")

    def log(self, message: str, attributes: Dict[str, str] = {}) -> None:
        common.eprint(self.maybe_prefix(message, attributes))
        self.drain_log_queue()
        self.log_line(message, attributes)

    def enqueue(self, message: Dict[str, str]) -> None:
        self.queue.put(message)

    def drain_log_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                attributes = {"machine": item["machine"], "type": "serial"}
                self.log_line(self.sanitise(item["msg"]), attributes)
        except Empty:
            pass

    @contextmanager
    def nested(self, message: str, attributes: Dict[str, str] = {}) -> Iterator[None]:
        common.eprint(self.maybe_prefix(message, attributes))

        self.xml.startElement("nest", attrs={})
        self.xml.startElement("head", attributes)
        self.xml.characters(message)
        self.xml.endElement("head")

        tic = time.time()
        self.drain_log_queue()
        yield
        self.drain_log_queue()
        toc = time.time()
        self.log("({:.2f} seconds)".format(toc - tic))

        self.xml.endElement("nest")
