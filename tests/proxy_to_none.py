from weakref import proxy

class D:
    inner = {'foo': 1, 'bar': 2}

class P:
    def __init__(self, d):
        self.prox = proxy(d, safe_weak_proxy_callback(self, 'prox'))

def safe_weak_proxy_callback(obj: object, attribute: str):
    """ Safe Weak Proxy Callback
        Provides a callback function that will return a variable used by WeakRef Proxy to None if the reference dies."""

    def back_to_none(_):  # A Weak Reference is passed in, but it is dead, and therefore useless, so using "_".
        setattr(obj, attribute, None)
    return back_to_none
    
d = D()
p = P(d)

print(f'While alive, prox.inner is: {p.prox.inner}')

if p.prox:
    print('prox is good')

print('deleting d')
del d

if p.prox:
    print('deleted prox is not None')
else:
    print('delteted prox is None')

print('printing prox type:')
print(type(p.prox))

if p.prox:
    print(p.prox.inner)
else:
    print('skipping inner print')
