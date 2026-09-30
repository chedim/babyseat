"""Experiments-isolation: a LAB field must never join the shared substrate.

The observed failure: a dream-loop probe built Membrane(field_path=in-a-
tempdir), the store auto-detected the running local Couchbase and -- bucket
creation refused (cluster RAM quota full) -- silently REUSED a foreign bucket
(cortex-core), landing lab rows in another project's memories. Auto-detect is
for a membrane's OWN field; the lab (tempdirs, experiments/) opts in
explicitly if it ever wants sharing. The guard now lives in substrate.py
(shared by every backend that can auto-detect a cluster).
"""
import os
import tempfile
from pathlib import Path

from membrane.substrate import _is_lab_field_path


def test_lab_tempdir_path_is_lab():
    with tempfile.TemporaryDirectory(prefix="dream-loop-") as d:
        assert _is_lab_field_path(os.path.join(d, "field-test.json")) is True


def test_regular_membrane_path_is_not_lab():
    p = Path("/Users/somewhere/myproject/.membrane/field.json")
    assert _is_lab_field_path(str(p)) is False


def test_experiments_field_pattern_is_not_lab():
    # experiments/*.py keep their own fields OUTSIDE tempdirs; the guard keys
    # on tempdir/experiments-dir + the field-<tag>.json naming convention.
    p = Path.cwd() / "experiments" / "scratch-field-x.json"
    assert _is_lab_field_path(str(p)) is False  # only field-<tag>.json is lab-belonging


def test_experiments_field_tag_pattern_is_lab():
    p = Path.cwd() / "experiments" / "field-dream.json"
    assert _is_lab_field_path(str(p)) is True