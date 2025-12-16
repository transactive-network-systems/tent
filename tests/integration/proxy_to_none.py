"""
Test suite for safe_weak_proxy_callback functionality.

Tests the weak reference proxy mechanism used throughout TENT to handle
circular references and automatic cleanup of dead references.
"""
import pytest
from weakref import proxy, ProxyType

from tent.utils.helpers import safe_weak_proxy_callback


class DummyObject:
    """Test object with some data."""
    inner = {'foo': 1, 'bar': 2}


class ProxyContainer:
    """Container that holds a weak proxy to another object."""
    def __init__(self, target):
        self.prox = proxy(target, safe_weak_proxy_callback(self, 'prox'))


def test_proxy_while_alive():
    """Test that proxy works correctly while the target is alive."""
    d = DummyObject()
    p = ProxyContainer(d)
    
    # Verify proxy is accessible and works
    assert p.prox is not None
    assert isinstance(p.prox, ProxyType)
    assert p.prox.inner == {'foo': 1, 'bar': 2}


def test_proxy_truthiness_while_alive():
    """Test that proxy evaluates to True while target is alive."""
    d = DummyObject()
    p = ProxyContainer(d)
    
    assert p.prox, "Proxy should be truthy while target is alive"


def test_proxy_set_to_none_when_target_deleted():
    """Test that proxy is set to None when the target object is deleted."""
    d = DummyObject()
    p = ProxyContainer(d)
    
    # Verify initial state
    assert p.prox is not None
    assert p.prox.inner == {'foo': 1, 'bar': 2}
    
    # Delete the target
    del d
    
    # After deletion, callback should have set prox to None
    assert p.prox is None, "Proxy should be None after target deletion"


def test_proxy_type_after_deletion():
    """Test that proxy attribute becomes NoneType after target deletion."""
    d = DummyObject()
    p = ProxyContainer(d)
    
    del d
    
    assert type(p.prox).__name__ == 'NoneType'
    assert p.prox is None


def test_cannot_access_inner_after_deletion():
    """Test that accessing inner attributes is safe after target deletion."""
    d = DummyObject()
    p = ProxyContainer(d)
    
    del d
    
    # Should be None, so accessing .inner would raise AttributeError if we tried
    if p.prox:
        # This branch should not execute
        pytest.fail("Proxy should be None after target deletion")
    else:
        # Expected: proxy is None, so we skip accessing inner
        assert p.prox is None
