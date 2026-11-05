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


class _OwnedProcess(subprocess.Popen):
    def poll(self):
        # Keep waitpid and recording returncode in one interruption-free
        # section. Teardown can then trust that an unset returncode means
        # the group leader has not been reaped and its ID cannot be reused.
        with _defer_interrupt():
            return super().poll()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while (returncode := self.poll()) is None:
            delay = 0.01
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(self.args, timeout)
                delay = min(delay, remaining)
            time.sleep(delay)
        return returncode


def _signal_group(child, signum):
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        return False
    return True


def _stop_group(child, term_grace):
    # Hold the unreaped leader's PID until after the final group signal.
    try:
        if _signal_group(child, signal.SIGTERM):
            time.sleep(term_grace)
            _signal_group(child, signal.SIGKILL)
    except PermissionError:
        # macOS can return EPERM when only zombies remain. The bounded wait
        # below still has to confirm that the owned leader exited.
        if sys.platform != "darwin":
            raise
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
                child = _OwnedProcess(args, start_new_session=True, **kwargs)
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
