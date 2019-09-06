#! /somewhere/python3

from __future__ import print_function
from xml.sax.saxutils import XMLGenerator
import _thread
import os
import pty
import queue
import re
import socket
import subprocess
import sys
import time
import unicodedata

CHAR_TO_KEY = {
    'A': 'shift-a', 'N': 'shift-n',  '-': '0x0C', '_': 'shift-0x0C',
    'B': 'shift-b', 'O': 'shift-o',  '=': '0x0D', '+': 'shift-0x0D',
    'C': 'shift-c', 'P': 'shift-p',  '[': '0x1A', '{': 'shift-0x1A',
    'D': 'shift-d', 'Q': 'shift-q',  ']': '0x1B', '}': 'shift-0x1B',
    'E': 'shift-e', 'R': 'shift-r',  ';': '0x27', ':': 'shift-0x27',
    'F': 'shift-f', 'S': 'shift-s',  "'": '0x28', '"': 'shift-0x28',
    'G': 'shift-g', 'T': 'shift-t',  '`': '0x29', '~': 'shift-0x29',
    'H': 'shift-h', 'U': 'shift-u', '\\': '0x2B', '|': 'shift-0x2B',
    'I': 'shift-i', 'V': 'shift-v',  ',': '0x33', '<': 'shift-0x33',
    'J': 'shift-j', 'W': 'shift-w',  '.': '0x34', '>': 'shift-0x34',
    'K': 'shift-k', 'X': 'shift-x',  '/': '0x35', '?': 'shift-0x35',
    'L': 'shift-l', 'Y': 'shift-y',  ' ': 'spc',
    'M': 'shift-m', 'Z': 'shift-z', '\n': 'ret',
    '!': 'shift-0x02', '@': 'shift-0x03', '#': 'shift-0x04', '$': 'shift-0x05',
    '%': 'shift-0x06', '^': 'shift-0x07', '&': 'shift-0x08', '*': 'shift-0x09',
    '(': 'shift-0x0A', ')': 'shift-0x0B',
    }


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def create_vlan(vlan_nr):
    global log
    log.log('starting VDE switch for network {}'.format(vlan_nr))
    vde_socket = os.path.abspath('./vde{}.ctl'.format(vlan_nr))
    pty_master, pty_slave = pty.openpty()
    vde_process = subprocess.Popen(
        'vde_switch -s {} --dirmode 0777'.format(vde_socket),
        bufsize=0,  # decide what buffering is appropriate
        stdin=pty_slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        )
    fd = os.fdopen(pty_master, 'w')
    fd.write('version\n')
    # TODO: perl version checks if this can be read from
    # an if not, dies. we could hang here forever. Fix it.
    vde_process.stdout.readline()
    if not os.path.exists(os.path.join(vde_socket, 'ctl')):
        raise Exception("cannot start vde_switch")

    return (vlan_nr, vde_socket, vde_process, fd)


class Logger:
    def __init__(self):
        self.logfile = os.environ.get('LOGFILE', '/dev/null')
        self.logfile_handle = open(self.logfile, 'wb')
        self.xml = XMLGenerator(self.logfile_handle, encoding='utf-8')
        self.queue = queue.Queue(1000)

        self.xml.startDocument()
        self.xml.startElement('logfile', attrs={})

    def close(self):
        self.xml.endElement('logfile')
        self.xml.endDocument()
        self.logfile_handle.close()

        file = open(self.logfile, 'r')
        print(file.read())

    def sanitise(self, message):
        return "".join(ch for ch in message
                       if unicodedata.category(ch)[0] != 'C')

    def maybe_prefix(self, message, attributes):
        if 'machine' in attributes:
            return '{}: {}'.format(attributes['machine'], message)
        return message

    def log_line(self, message, attributes):
        self.xml.startElement('line', attributes)
        self.xml.characters(message)
        self.xml.endElement('line')

    def log(self, message, attributes={}):
        eprint(self.maybe_prefix(message, attributes))
        self.drain_log_queue()
        self.log_line(message, attributes)

    def enqueue(self, message):
        self.queue.put(message)

    def drain_log_queue(self):
        try:
            while True:
                item = self.queue.get_nowait()
                attributes = {
                    'machine': item['machine'],
                    'type': 'serial'
                    }
                self.log_line(self.sanitise(item['msg']), attributes)
        except queue.Empty:
            pass

    def nest_enter(self, message, attributes={}):
        eprint(self.maybe_prefix(message, attributes))
        print(message)

        self.xml.startElement('nest', attributes)
        self.xml.startElement('head', attributes)
        self.xml.characters(message)
        self.xml.endElement('head')

        tic = time.time()
        self.drain_log_queue()

        return tic

    def nest_exit(self, tic):
        self.drain_log_queue()
        toc = time.time()
        self.log("({:.2f} seconds)".format(toc - tic))

        self.xml.endElement('nest')

    class log_nest:
        def __init__(self, logger, message, attributes={}):
            self.message = message
            self.logger = logger
            self.attributes = attributes

        def __enter__(self):
            self.tic = self.logger.nest_enter(self.message, self.attributes)
            return

        def __exit__(self, type, value, traceback):
            self.logger.nest_exit(self.tic)

    def nested(self, message, attributes={}):
        return self.log_nest(self, message, attributes)


class Machine:
    def __init__(self, script, name, state_dir, shared_dir, log):
        self.script = script
        self.name = name
        self.state_dir = state_dir
        self.shared_dir = shared_dir
        self.booted = False
        self.connected = False
        self.pid = 0
        self.socket = None
        self.monitor = None
        self.logger = log
        self.allow_reboot = False

    def is_up(self):
        return self.booted and self.connected

    def log(self, msg):
        self.logger.log(msg, {'machine': self.name})

    def nested(self, msg, attrs={}):
        my_attrs = {'machine': self.name}
        my_attrs.update(attrs)
        return self.logger.nested(msg, my_attrs)

    def wait_for_monitor_prompt(self):
        while True:
            answer = self.monitor.recv(1024).decode()
            if answer.endswith('(qemu) '):
                return answer

    def send_monitor_command(self, command):
        message = ('{}\n'.format(command)).encode()
        self.log('sending monitor command: {}'.format(command))
        self.monitor.send(message)
        return self.wait_for_monitor_prompt()

    def wait_for_unit(self, unit, user=None):
        while True:
            info = self.get_unit_info(unit, user)
            state = info['ActiveState']
            if state == 'failed':
                raise Exception('unit "{}" reached state "{}"'.format(unit,
                                                                      state))

            if state == 'inactive':
                status, jobs = self.systemctl('list-jobs --full 2>&1', user)
                if 'No jobs' in jobs:
                    info = self.get_unit_info(unit)
                    if info['ActiveState'] == state:
                        raise Exception(('unit "{}" is inactive and there '
                                         'are no pending jobs').format(unit))
            if state == 'active':
                return True

    def get_unit_info(self, unit, user=None):
        status, lines = self.systemctl('--no-pager show "{}"'.format(unit),
                                       user)
        if status != 0:
            return None

        line_pattern = re.compile(r'^([^=]+)=(.*)$')

        def tuple_from_line(line):
            match = line_pattern.match(line)
            return match[1], match[2]

        return dict(tuple_from_line(line)
                    for line in lines.split('\n')
                    if line_pattern.match(line))

    def systemctl(self, q, user=None):
        if user is not None:
            q = q.replace('\'', '\\\'')
            return self.execute(('su -l {} -c '
                                 '$\'XDG_RUNTIME_DIR=/run/user/`id -u` '
                                 'systemctl --user {}\'').format(user, q))
        return self.execute('systemctl {}'.format(q))

    def execute(self, command):
        self.connect()

        out_command = '( {} ); echo \'|!EOF\' $?\n'.format(command)
        self.shell.send(out_command.encode())

        output = ''
        status_code_pattern = re.compile(r'(.*)\|\!EOF\s+(\d+)')

        while True:
            chunk = self.shell.recv(4096).decode()
            match = status_code_pattern.match(chunk)
            if match:
                output += match[1]
                status_code = int(match[2])
                return (status_code, output)
            output += chunk

    def succeed(self, command):
        with self.nested('must succeed: {}'.format(command)):
            status, output = self.execute(command)
            if status != 0:
                self.log('output: {}'.format(output))
                raise Exception('command `{}` did not succeed (exit code {})'
                                .format(command, status))
            return output

    def fail(self, command):
        with self.nested('must fail: {}'.format(command)):
            status, output = self.execute(command)
            if status == 0:
                raise Exception('command `{}` unexpectedly succeeded'
                                .format(command))

    def wait_until_succeeds(self, command):
        with self.nested('waiting for success: {}'.format(command)):
            while True:
                status, output = self.execute(command)
                if status == 0:
                    return output

    def wait_until_fails(self, command):
        with self.nested('waiting for failure: {}'.format(command)):
            while True:
                status, output = self.execute(command)
                if status != 0:
                    return output

    def wait_for_shutdown(self):
        if not self.booted:
            return

        with self.nested('waiting for the VM to power off'):
            sys.stdout.flush()
            self.process.wait()

            self.pid = 0
            self.booted = False
            self.connected = False

    def get_tty_text(self, tty):
        status, output = self.execute("fold -w$(stty -F /dev/tty{0} size | "
                                      "awk '{{print $2}}') /dev/vcs{0}"
                                      .format(tty))
        return output

    def wait_until_tty_matches(self, tty, regexp):
        matcher = re.compile(regexp)
        with self.nested('waiting for {} to appear on tty {}'
                         .format(regexp, tty)):
            while True:
                text = self.get_tty_text(tty)
                if len(matcher.findall(text)) > 0:
                    return True

    def send_chars(self, chars):
        with self.nested('sending keys ‘{}‘'.format(chars)):
            for char in chars:
                self.send_key(char)

    def wait_for_file(self, filename):
        with self.nested('waiting for file ‘{}‘'.format(filename)):
            while True:
                status, _ = self.execute('test -e {}'.format(filename))
                if status == 0:
                    return True

    def wait_for_open_port(self, port):
        with self.nested('waiting for TCP port {}'.format(port)):
            while True:
                status, _ = self.execute('nc -z localhost {}'.format(port))
                if status == 0:
                    return True

    def stop_job(self, jobname, user=None):
        self.systemctl('stop {}'.format(jobname), user)

    def connect(self):
        if self.connected:
            return

        with self.nested('waiting for the VM to finish booting'):
            self.start()

            tic = time.time()
            self.shell.recv(1024)
            # TODO: Timeout
            toc = time.time()

            self.log('connected to guest root shell')
            self.log('(connecting took {:.2f} seconds)'.format(toc - tic))
            self.connected = True

    def screenshot(self, filename):
        out_dir = os.environ.get('out', os.getcwd())
        word_pattern = re.compile(r'^\w+$')
        if word_pattern.match(filename):
            filename = os.path.join(out_dir, '{}.png'.format(filename))
        tmp = '{}.ppm'.format(filename)

        print('making screenshot {}'.format(filename))
        self.send_monitor_command('screendump {}'.format(tmp))
        ret = subprocess.run('pnmtopng {} > {}'.format(tmp, filename),
                             shell=True)
        os.unlink(tmp)
        if ret.returncode != 0:
            raise Exception('Cannot convert screenshot')

    def send_key(self, key):
        key = CHAR_TO_KEY.get(key, key)
        self.send_monitor_command('sendkey {}'.format(key))

    def start(self):
        if self.booted:
            return

        self.log('starting vm')

        def create_socket(path):
            if os.path.exists(path):
                os.unlink(path)
            s = socket.socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM)
            s.bind(path)
            s.listen(1)
            return s

        monitor_path = os.path.join(self.state_dir, 'monitor')
        self.monitor_socket = create_socket(monitor_path)

        shell_path = os.path.join(self.state_dir, 'shell')
        self.shell_socket = create_socket(shell_path)

        qemu_options = ' '.join([
            '' if self.allow_reboot else '-no-reboot',
            '-monitor unix:{}'.format(monitor_path),
            '-chardev socket,id=shell,path={}'.format(shell_path),
            '-device virtio-serial',
            '-device virtconsole,chardev=shell',
            '-device virtio-rng-pci',
            '-serial stdio' if 'DISPLAY' in os.environ else '-nographic'
            ]) + ' ' + os.environ.get('QEMU_OPTS', '')

        environment = {
            'QEMU_OPTS': qemu_options,
            'SHARED_DIR': self.shared_dir,
            'USE_TMPDIR': '1',
        }
        environment.update(dict(os.environ))

        self.process = subprocess.Popen(
            self.script,
            bufsize=0,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            cwd=self.state_dir,
            env=environment,
            )
        self.monitor, _ = self.monitor_socket.accept()
        self.shell, _ = self.shell_socket.accept()

        def process_serial_output():
            for line in self.process.stdout:
                line = line.decode().replace('\r', '').rstrip()
                eprint('{} # {}'.format(self.name, line))
                self.logger.enqueue({'msg': line, 'machine': self.name})

        _thread.start_new_thread(process_serial_output, ())

        self.wait_for_monitor_prompt()

        self.pid = self.process.pid
        self.booted = True

        self.log('QEMU running (pid {})'.format(self.pid))


log = Logger()


def create_machine(script_path):
    global log
    try:
        name = re.search('run-(.+)-vm$', script_path).group(1)
    except AttributeError:
        name = 'machine'
    tmp_dir = os.environ.get('TMPDIR', '/tmp')
    shared_dir = os.path.join(tmp_dir, 'xchg-shared')
    os.makedirs(shared_dir, mode=0o700, exist_ok=True)
    state_dir = os.path.join(tmp_dir, 'vm-state-{}'.format(name))
    os.makedirs(state_dir, mode=0o700, exist_ok=True)

    return Machine(script_path, name, state_dir, shared_dir, log)


vlan_nrs = list(dict.fromkeys(os.environ['VLANS'].split()))

vde_sockets = [create_vlan(v) for v in vlan_nrs]

for nr, vde_socket, _, _ in vde_sockets:
    os.environ['QEMU_VDE_SOCKET_{}'.format(nr)] = vde_socket

sys.stdout.flush()

vm_scripts = sys.argv[1:]

machines = [create_machine(s) for s in vm_scripts]
machine_eval = ['{0} = machines[{1}]'.format(m.name, idx)
                for idx, m in enumerate(machines)]
exec('\n'.join(machine_eval))


def start_all():
    with log.nested("starting all VMs"):
        for machine in machines:
            machine.start()


def run_tests():
    with log.nested('running the VM test script'):
        test_script = os.environ['testScript']
        try:
            exec(test_script)
        except Exception as e:
            eprint('error: {}'.format(str(e)))
            sys.exit(1)

    # TODO: Collect coverage data

    for machine in machines:
        if machine.is_up():
            machine.execute('sync')

    if nr_tests != 0:
        log.log('{} out of {} tests succeeded'.format(nr_succeeded, nr_tests))


class subtest:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        global nr_tests
        self.tic = log.nest_enter(self.name)
        nr_tests += 1

        return

    def __exit__(self, exc_type, exc_value, traceback):
        global nr_succeeded
        if exc_type is None:
            nr_succeeded += 1
        else:
            log.log('error: {}'.format(str(exc_value)))

        log.nest_exit(self.tic)

        if exc_type is not None:
            return True


nr_tests = 0
nr_succeeded = 0

tic = time.time()
run_tests()
toc = time.time()
print("test script finished in {:.2f}s".format(toc - tic))

log.close()
