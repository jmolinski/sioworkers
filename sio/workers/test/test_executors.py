import glob
import os.path
import shutil

from nose.tools import ok_, eq_, assert_not_equal, nottest, raises, \
        assert_raises
from filetracker.dummy import DummyClient

from sio.compilers.job import run as run_compiler
from sio.executors.common import run as run_executor
from sio.workers import ft
from sio.workers.execute import execute
from sio.workers.executors import UnprotectedExecutor, \
        DetailedUnprotectedExecutor, SupervisedExecutor, VCPUExecutor, \
        ExecError

# sio2-executors tests
#
# Source files used for testing are located in
# sources/ directory
#
# A few notes:
#
# 1) This module uses test generators intensely. This
#    approach makes adding tests for new compilers a breeze.
#    For more details about this technique, see:
# http://readthedocs.org/docs/nose/en/latest/writing_tests.html#test-generators
#
# 2) By default, executors are excluded from the
#    testing, due to their rather unwieldy dependencies. You
#    can enable them by setting environment variable TEST_SANDBOXES to 1.
#    All needed dependencies will be downloaded automagically
#    by sio.workers.sandbox.
#

SOURCES = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'sources')
ENABLE_SANDBOXES = os.environ.get('TEST_SANDBOXES', False)

def in_(a, b, msg=None):
    """Shorthand for 'assert a in b, "%r not in %r" % (a, b)"""
    if a not in b:
        raise AssertionError(msg or "%r not in %r" % (a, b))

def not_in_(a, b, msg=None):
    """Shorthand for 'assert a not in b, "%r not in %r" % (a, b)"""
    if a in b:
        raise AssertionError(msg or "%r not in %r" % (a, b))

class TemporaryCwd(object):
    "Helper class for changing the working directory."

    def __init__(self):
        self.path = os.tmpnam()

    def __enter__(self):
        os.mkdir(self.path)
        self.previousCwd = os.getcwd()
        os.chdir(self.path)

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.chdir(self.previousCwd)
        shutil.rmtree(self.path)

def upload_files():
    "Uploads all files from SOURCES to a newly created dummy filetracker"

    # DummyClient operates in the working directory.
    ft.set_instance(DummyClient())
    for path in glob.glob(os.path.join(SOURCES, '*')):
        ft.upload({'path': '/' + os.path.basename(path)}, 'path', path)

def compile(source):
    compiler_env = {
        'source_file': source,
        'compiler':  ENABLE_SANDBOXES and 'default-c' or 'system-c',
        'out_file': '/exe',
    }

    # Dummy sandbox doesn't support asking for versioned filename
    out_file = compiler_env['out_file']
    result_env = run_compiler(compiler_env)
    print_env(result_env)

    eq_(result_env['result_code'], 'OK')
    return out_file

def compile_and_execute(source, executor, **exec_args):
    exe_file = compile(source)
    ft.download({'exe_file': exe_file}, 'exe_file', 'exe')
    os.chmod('exe', 0700)
    ft.download({'in_file': '/input'}, 'in_file', 'in')

    with executor as e:
        with open('in', 'rb') as inf:
                renv = e(['./exe'], stdin=inf, **exec_args)

    return renv

def compile_and_run(source, executor_env, executor, safe_check=False):
    executor_env['exe_file'] = compile(source)
    return run_executor(executor_env, executor, safe_check=safe_check)

def print_env(env):
    from pprint import pprint
    pprint(env)

def fail(*args, **kwargs):
    ok_(False, "Forced fail")

MEMORY_CHECKS = ['30MiB-bss.c', '30MiB-data.c', '30MiB-malloc.c',
        '30MiB-stack.c']
MEMORY_CHECKS_LIMIT = 30*1024 # in KiB
CHECKING_EXECUTORS = [DetailedUnprotectedExecutor]
SANDBOXED_CHECKING_EXECUTORS = [SupervisedExecutor, VCPUExecutor]

def test_common_memory_limiting():
    def _test(source, mem_limit, executor, callback):
        with TemporaryCwd():
            upload_files()
            result_env = compile_and_run(source, {
                'in_file': '/input',
                'exec_mem_limit': mem_limit
                }, executor)
            print_env(result_env)
            callback(result_env)

    def status_ok(env):
        eq_(env['result_code'], 'OK')

    def status_not_ok(env):
        assert_not_equal(env['result_code'], 'OK')

    def status_mle(env):
        status_not_ok(env)
        if env['result_code'] != 'MLE':
            eq_(env['result_code'], 'RE')
            in_('signal', env['result_string']) # sigsegv
            in_('11', env['result_string'])

    for test in MEMORY_CHECKS:
        for executor in CHECKING_EXECUTORS:
            yield _test, "/" + test, int(MEMORY_CHECKS_LIMIT*1.2), executor(), \
                    status_ok
            yield _test, "/" + test, int(MEMORY_CHECKS_LIMIT*0.9), executor(), \
                    status_not_ok

        if ENABLE_SANDBOXES:
            for executor in SANDBOXED_CHECKING_EXECUTORS:
                yield _test, "/" + test, int(MEMORY_CHECKS_LIMIT*1.2), \
                        executor(),  status_ok
                yield _test, "/" + test, int(MEMORY_CHECKS_LIMIT*0.9), \
                        executor(),  status_mle

def test_common_time_limiting():
    def _test(source, time_limit, executor, callback):
        with TemporaryCwd():
            upload_files()
            result_env = compile_and_run(source, {
                'in_file': '/input',
                'exec_time_limit': time_limit
            }, executor)
            print_env(result_env)
            callback(result_env)

    def status_ok(env):
        eq_(env['result_code'], 'OK')

    def status_tle(env):
        eq_(env['result_code'], 'TLE')

    for executor in CHECKING_EXECUTORS:
        yield _test, '/procspam.c', 1000, executor(), status_tle

    if ENABLE_SANDBOXES:
        for executor in SANDBOXED_CHECKING_EXECUTORS:
            yield _test, "/procspam.c", 1000, executor(), status_tle
            yield _test, "/1-sec-prog.c", 10, executor(), status_tle

        yield _test, "/1-sec-prog.c", 1000, SupervisedExecutor(), status_ok
        yield _test, "/1-sec-prog.c", 1100, VCPUExecutor(), status_ok

def test_outputting_non_utf8():
    if ENABLE_SANDBOXES:
        with TemporaryCwd():
            upload_files()
            renv = compile_and_run('/output-non-utf8.c', {
                    'in_file': '/input',
                    'check_output': True,
                    'hint_file': '/input',
                    }, SupervisedExecutor(), safe_check=True)
            print_env(renv)
            in_('42', renv['result_string'])
            ok_(renv['result_string'].decode('utf8'))

def test_uploading_out():
    with TemporaryCwd():
        upload_files()
        renv = compile_and_run('/add_print.c', {
            'in_file': '/input',
            'out_file': '/output',
            'upload_out': True,
        }, DetailedUnprotectedExecutor())
        print_env(renv)

        ft.download({'path': '/output'}, 'path', 'd_out')
        in_('84', open('d_out').read())

# Direct tests
@nottest
def _test_exec(source, executor, callback, kwargs):
    with TemporaryCwd():
        upload_files()
        result_env = compile_and_execute(source, executor, **kwargs)
        print_env(result_env)
        callback(result_env)

@nottest
def _test_raw(cmd, executor, callback, kwargs):
    with TemporaryCwd():
        with executor:
            result_env = executor(cmd, **kwargs)
        print_env(result_env)
        callback(result_env)

def test_capturing_stdout():
    def only_stdout(env):
        in_('stdout', env['stdout'])
        not_in_('stderr', env['stdout'])

    def with_stderr(env):
        in_('stdout', env['stdout'])
        in_('stderr', env['stdout'])

    def lines_split(env):
        ok_(isinstance(env['stdout'], list))
        eq_(len(env['stdout']), 3)

    executors = [UnprotectedExecutor]
    if ENABLE_SANDBOXES:
        executors += [VCPUExecutor]

    for executor in executors:
        yield _test_exec, '/add_print.c', executor(), only_stdout, \
                {'capture_output': True}
        yield _test_exec, '/add_print.c', executor(), lines_split, \
                {'capture_output': True, 'split_lines': True}
        yield _test_exec, '/add_print.c', executor(), with_stderr, \
                {'capture_output': True, 'forward_stderr': True}


def test_return_codes():
    _raising_test_exec = raises(ExecError)(_test_exec)

    def return_42(env):
        eq_(42, env['return_code'])

    def runtime_42(env):
        eq_('RE', env['result_code'])
        in_('42', env['result_string'])

    executors = [UnprotectedExecutor]

    for executor in executors:
        yield _raising_test_exec, '/return-scanf.c', executor(), None, {}
        yield _test_exec, '/return-scanf.c', executor(), return_42, \
                {'ignore_errors': True}
        yield _test_exec, '/return-scanf.c', executor(), return_42, \
            {'extra_ignore_errors': (42,)}

    checking_executors = CHECKING_EXECUTORS
    if ENABLE_SANDBOXES:
        checking_executors += SANDBOXED_CHECKING_EXECUTORS

    for executor in checking_executors:
        yield _test_exec, '/return-scanf.c', executor(), runtime_42, {}

def test_output_limit():
    def ole(env):
        eq_('OLE', env['result_code'])

    def stdout_shorter(limit):
        def inner(env):
            ok_(len(env['stdout']) <= limit)
        return inner

    executors = [UnprotectedExecutor]

    for executor in executors:
        yield _test_exec, '/add_print.c', executor(), stdout_shorter(10), \
                {'capture_output': True, 'output_limit': 10}

    checking_executors = [] # UnprotectedExecutor doesn't support OLE
    if ENABLE_SANDBOXES:
        checking_executors += SANDBOXED_CHECKING_EXECUTORS

    for executor in checking_executors:
        yield _test_exec, '/add_print.c', executor(), ole, \
            {'output_limit': 10}
        yield _test_exec, '/iospam-hard.c', executor(), ole, {} # Default

def test_signals():
    def due_signal(code):
        def inner(env):
            eq_('RE', env['result_code'])
            in_('due to signal', env['result_string'])
            in_(str(code), env['result_string'])
        return inner


    checking_executors = CHECKING_EXECUTORS
    if ENABLE_SANDBOXES:
        checking_executors += SANDBOXED_CHECKING_EXECUTORS

    SIGNALS_CHECKS = [
            ('sigabrt.c', 6),
            ('sigfpe.c', 8),
            ('sigsegv.c', 11),
            ]

    for executor in checking_executors:
        for (prog, code) in SIGNALS_CHECKS:
            yield _test_exec, '/' + prog, executor(), due_signal(code), {}

def test_rule_violation():
    def rv(msg):
        def inner(env):
            eq_('RV', env['result_code'])
            in_(msg, env['result_string'])
        return inner

    checking_executors = []
    if ENABLE_SANDBOXES:
        checking_executors += SANDBOXED_CHECKING_EXECUTORS

    for executor in checking_executors:
        yield _test_exec, '/open.c', executor(), rv('opening files'), {}

def test_vcpu_accuracy():
    def used_1sec(env):
        eq_('OK', env['result_code'])
        eq_(1000, env['time_used'])

    if ENABLE_SANDBOXES:
        yield _test_exec, '/1-sec-prog.c', VCPUExecutor(), used_1sec, {}

def test_real_time_limit():
    def real_tle(limit):
        def inner(env):
            eq_('TLE', env['result_code'])
            ok_(env['real_time_used'] > limit)
        return inner

    def syscall_limit(env):
        eq_('TLE', env['result_code'])
        in_('syscalls', env['result_string'])

    checking_executors = CHECKING_EXECUTORS
    if ENABLE_SANDBOXES:
        checking_executors += [VCPUExecutor] # FIXME: Supervised ignores realtime

    for executor in checking_executors:
        yield _test_exec, '/procspam.c', executor(), real_tle, \
            {'real_time_limit': 1000, 'time_limit': 10000}

    for executor in CHECKING_EXECUTORS:
        yield _test_exec, '/iospam.c', executor(), real_tle, \
            {'real_time_limit': 1000, 'time_limit': 10000}

    if ENABLE_SANDBOXES:
        yield _test_exec, '/iospam.c', VCPUExecutor(), syscall_limit, \
            {'time_limit': 500}

# Raw executing
def test_execute():
    with TemporaryCwd():
        rc, out = execute(['echo', '2'])
        eq_(rc, 0)
        eq_(out, '2\n')
        rc, out = execute(['exit', '1'], ignore_errors=True)
        eq_(rc, 1)

        assert_raises(ExecError, execute, ['exit', '1'])

        rc, out = execute(['mkdir', 'spam'])
        eq_(rc, 0)
        rc, out = execute(['ls'])
        in_('spam', out)

