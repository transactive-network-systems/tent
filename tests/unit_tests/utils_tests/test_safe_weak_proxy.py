"""safe_weak_proxy_callback: a weak proxy that becomes None when its target is collected."""
from weakref import proxy, ProxyType

from tent.utils.helpers import safe_weak_proxy_callback


class Target:
    inner = {'foo': 1, 'bar': 2}


class Holder:
    def __init__(self, target):
        self.prox = proxy(target, safe_weak_proxy_callback(self, 'prox'))


def test_proxy_forwards_to_a_live_target():
    target = Target()
    holder = Holder(target)
    assert isinstance(holder.prox, ProxyType)
    assert holder.prox and holder.prox.inner == {'foo': 1, 'bar': 2}


def test_proxy_attribute_is_none_once_the_target_is_collected():
    target = Target()
    holder = Holder(target)
    del target
    assert holder.prox is None


def test_callback_targets_the_named_attribute_only():
    target = Target()
    holder = Holder(target)
    holder.other = proxy(Target())  # unrelated proxy on the same holder
    del target
    assert holder.prox is None and holder.other is not None
