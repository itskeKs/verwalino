"""The memory of the inbox's model stage (review of PR #832, round 2, item 6).

The declaration worker runs the stage in a container of 768 MiB and starts a child for the local parse of a PDF and a child for
the decode of a PNG or JPEG. A child that needs more than the container has left must be stopped by its own address-space
bound and not by the container (``core/document_limits.py``). No test here starts a container.
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.core import image_reading, intake_extraction
from app.core.document_limits import CHILD_MEMORY_BYTES, CONTAINER_MEMORY_BYTES, WORKER_RESERVE_BYTES
from tests.test_invoice_claimants import INBOX_STAGE, STACKS, services

BACKEND = Path(__file__).resolve().parents[1]
UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def mem_limit_bytes(raw):
    """The bytes of a Compose ``mem_limit`` (``768m``, ``4g``)."""
    match = re.fullmatch(r"(\d+)\s*([bkmg]?)", str(raw).lower())
    assert match, raw
    return int(match.group(1)) * UNITS[match.group(2)]


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_the_container_that_runs_the_stage_has_the_memory_the_documented_bound_is_taken_from(stack):
    for name in INBOX_STAGE[stack]:
        assert mem_limit_bytes(services(stack)[name]["mem_limit"]) == CONTAINER_MEMORY_BYTES, f"{stack}/{name}: change the documented constants with the container"


@pytest.mark.no_db
def test_a_child_is_stopped_by_its_own_bound_before_the_container_runs_out_with_room_for_the_worker():
    assert CHILD_MEMORY_BYTES + WORKER_RESERVE_BYTES <= CONTAINER_MEMORY_BYTES
    assert CHILD_MEMORY_BYTES < CONTAINER_MEMORY_BYTES < 1024**3, "the former bound of 1 GiB was above what the container has"


@pytest.mark.no_db
def test_the_local_parse_and_the_image_decode_use_the_one_bound():
    assert intake_extraction.CHILD_MEMORY_BYTES == image_reading.CHILD_MEMORY_BYTES == CHILD_MEMORY_BYTES


def address_space_limit(command):
    """The soft limit of the address space of a started child, read from the kernel once the child has set it; Linux only."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=BACKEND, start_new_session=True)
    try:
        line = ""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and str(CHILD_MEMORY_BYTES) not in line.split():
            time.sleep(0.05)
            line = next((row for row in Path(f"/proc/{process.pid}/limits").read_text().splitlines() if row.startswith("Max address space")), "")
        return line.split()[3:5]
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()


@pytest.mark.no_db
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the address-space limit is applied on Linux only")
@pytest.mark.parametrize("command", [
    [sys.executable, "-m", "app.core.intake_extraction", "application/pdf", "[1, 1, 1]"],
    [*image_reading.CHILD_COMMAND, "20000000", "4000000", "30"],
], ids=["local-parse", "image-decode"])
def test_each_child_runs_under_the_address_space_limit(command):
    assert address_space_limit(command) == [str(CHILD_MEMORY_BYTES)] * 2
