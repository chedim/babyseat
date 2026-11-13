"""A time kill (`Trip:<node>`) comes from the VM host, not from a c1f setter, so the journal must record it as an OUTSIDE write
(it cannot be re-derived on replay: it depends on how long something took). Every other engine write stays `trigger:*` (derived)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import fields_live as FL  # noqa: E402
from membrane import journal as J  # noqa: E402


def test_a_trip_write_is_not_stamped_as_a_vm_write():
    w = FL.stamp_writer("field:driver:trip")
    assert not w.startswith(J.VM_WRITER), w


def test_every_other_engine_write_stays_a_vm_write():
    for name in ("field:plan", "field:driver:file", "field:trip-clear", "field:intend-create", ""):
        assert FL.stamp_writer(name).startswith(J.VM_WRITER), name


def test_a_trip_clear_stays_derived():
    # the clear is re-derived on replay (a fresh evaluation that does not trip), so it is not journalled
    assert FL.stamp_writer("field:trip-clear").startswith(J.VM_WRITER)


def test_a_trip_birth_is_stamped_like_its_writes():
    assert not FL.stamp_born_writer("Trip").startswith(J.VM_WRITER)
    assert FL.stamp_born_writer("Goal").startswith(J.VM_WRITER)
