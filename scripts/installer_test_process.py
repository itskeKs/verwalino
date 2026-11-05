"""Process ownership for the fake-Docker installer contracts (POSIX only)."""

from contextlib import contextmanager
import os
import signal
import subprocess
import sys
import time
import unittest


TERM_GRACE_SECONDS = 1
KILL_WAIT_SECONDS = 5
_signal_received = None
_deferred = 0
_managed = 0


class _StopProcess(BaseException):
    """Unwind without Popen's KeyboardInterrupt handler reaping the group leader."""


class _StopSuite(KeyboardInterrupt):
    """Unwind without unittest treating the interruption as a test error."""


def _raise_interrupt():
    if _managed:
        raise _StopProcess
    raise _StopSuite


def _interrupt(signum, _frame):
    global _signal_received
    if _signal_received is None:
        _signal_received = signum
        if not _deferred:
            _raise_interrupt()


def install_interrupt_handlers():
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _interrupt)


@contextmanager
def _defer_interrupt():
    global _deferred
    already_interrupted = _signal_received is not None
    _deferred += 1
    try:
        yield
    finally:
        _deferred -= 1
        if not already_interrupted and not _deferred and _signal_received is not None:
            _raise_interrupt()


def _child_status(child):
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def _signal_group(child, signum):
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS returns EPERM for a group containing only zombies.
        if sys.platform != "darwin" or _child_status(child) is None:
            raise
        return False
    return True


def _stop_group(child, term_grace):
    # A signal can interrupt Popen just after waitpid, before it records
    # returncode. Confirm the leader is still our child without reaping it,
    # and hold its PID until after the final group signal.
    try:
        _child_status(child)
    except ChildProcessError:
        return
    try:
        if _signal_group(child, signal.SIGTERM):
            time.sleep(term_grace)
            _signal_group(child, signal.SIGKILL)
    finally:
        child.wait(timeout=KILL_WAIT_SECONDS)


def _finish(child, term_grace):
    if child is None:
        return
    with _defer_interrupt():
        try:
            # Successful commands join their children normally.
            if child.returncode is None:
                _stop_group(child, term_grace)
        finally:
            for pipe in (child.stdin, child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()


@contextmanager
def managed_process(args, *, term_grace=TERM_GRACE_SECONDS, **kwargs):
    global _managed
    child = None
    _managed += 1
    try:
        try:
            # Do not let a signal land between spawning and taking ownership.
            # Deferring in Python avoids inheriting a blocked signal mask.
            with _defer_interrupt():
                child = subprocess.Popen(args, start_new_session=True, **kwargs)
            yield child
        finally:
            _finish(child, term_grace)
    except _StopProcess:
        # Also cover a first signal at the entry to the finally block,
        # before _finish has deferred delivery.
        _finish(child, term_grace)
        raise _StopSuite from None
    finally:
        _managed -= 1


def run(args, *, input=None, capture_output=False, timeout=30, check=False, **kwargs):
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with managed_process(args, **kwargs) as child:
        stdout, stderr = child.communicate(input, timeout=timeout)
    result = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def unittest_main():
    try:
        unittest.main(module="__main__")
    except _StopSuite:
        # TemporaryDirectory's finalizers remove test-owned roots even if
        # unittest was interrupted before it could run its addCleanup calls.
        raise SystemExit(128 + _signal_received) from None
