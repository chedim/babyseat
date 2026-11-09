"""The native VM is the only VM: no switch, no Python fallback; a missing extension raises loudly."""
import importlib
import sys

import pytest


def test_missing_extension_raises_loudly(monkeypatch):
    from membrane import ch
    monkeypatch.setitem(sys.modules, "membrane.membrane_vm", None)     # `import` of it now fails
    monkeypatch.delitem(sys.modules, "membrane.ch_native", raising=False)
    import membrane
    monkeypatch.delattr(membrane, "ch_native", raising=False)
    monkeypatch.delattr(membrane, "membrane_vm", raising=False)
    with pytest.raises(ImportError):
        ch.make_vm(object())
    with pytest.raises(ImportError):
        importlib.import_module("membrane.ch_native")


def test_no_switch_and_vm_is_native():
    from membrane import ch, ch_native
    assert ch.VM is ch_native.NativeVM
    assert not hasattr(ch, "PyVM") and not hasattr(ch, "NATIVE_ENV")
