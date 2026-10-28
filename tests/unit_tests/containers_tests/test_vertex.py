import pytest
from weakref import ProxyType

# Import the module and class under test
import tent.containers.vertex as mod
from tent.containers.vertex import Vertex


class DummyCurve:
    def __init__(self, id_="curve-1", data_manager=None):
        self.id = id_
        self.vertices = []
        self.data_manager = object() if data_manager is None else data_manager


@pytest.fixture(autouse=True)
def stub_archivable_init(monkeypatch):
    # Ensure Vertex.__init__ can call super().__init__ without side effects or args
    monkeypatch.setattr(mod.Archivable, "__init__", lambda self: None)


def test_record_with_real_list_and_weakref(monkeypatch):
    captured = {}

    def cb_factory(self_obj, name):
        captured["args"] = (self_obj, name)

        def on_gc(_):
            pass

        return on_gc

    monkeypatch.setattr(mod, "safe_weak_proxy_callback", cb_factory)

    curve = DummyCurve()
    v = Vertex(price=10.0, cost=5.0, quantity=100.0, curve=curve)
    curve.vertices.append(v)

    # curve is stored as a weakref proxy
    assert isinstance(v.curve, ProxyType)

    # record uses real list.index with the actual vertex
    assert v.record == 0

    # callback factory was called with (self, 'curve'): a dead curve clears the vertex's curve reference
    assert captured["args"][0] is v
    assert captured["args"][1] == "curve"


def test_defaults_and_continuity_is_validated(monkeypatch):
    calls = []

    def validate_bool(val, name):
        calls.append((val, name))
        return bool(val)

    monkeypatch.setattr(mod, "validate_bool", validate_bool)

    curve = DummyCurve()
    v = Vertex(price=1.5, curve=curve)  # continuity defaults to True
    assert v.cost == 0.0
    assert v.quantity == 0.0
    assert v.uncertainty == 0.0
    assert v.continuity is True
    assert calls == [(True, "continuity")]

    v2 = Vertex(price=1.5, continuity=0, curve=curve)
    assert v2.continuity is False
    assert calls[-1] == (0, "continuity")


def test_comparisons_and_hash():
    v1 = Vertex(price=1.0, quantity=5.0)
    v2 = Vertex(price=2.0, quantity=1.0)
    v3 = Vertex(price=1.0, quantity=5.0)

    assert v1 < v2
    assert v2 > v1
    assert v1 != v2
    assert v1 == v3
    assert hash(v1) == hash(v3)


def test_data_manager_property():
    dm = object()
    curve = DummyCurve(data_manager=dm)
    v = Vertex(price=1.0, curve=curve)
    assert v.data_manager is dm


def test_table_name_is_constant():
    v = Vertex(price=1.0)
    assert v.table_name == "vertex"


def test_get_record_includes_fields(monkeypatch):
    # Stub the base class get_record so we don't depend on external behavior
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {"id": "uuid-1"})

    curve = DummyCurve(id_="curve-123")
    v = Vertex(price=3.0, cost=2.0, quantity=10.0, continuity=True, uncertainty=0.25, curve=curve)
    curve.vertices.append(v)

    rec = v.get_record()
    assert rec["id"] == "uuid-1"
    assert rec["curve_id"] == "curve-123"
    assert rec["price"] == 3.0
    assert rec["quantity"] == 10.0
    assert rec["cost"] == 2.0
    assert rec["continuity"] is True
    assert rec["uncertainty"] == 0.25
    assert rec["record"] == 0


def test_get_record_without_curve_raises(monkeypatch):
    # Base get_record stubbed so we only test Vertex behavior
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {})

    v = Vertex(price=1.0)
    # Vertex.get_record accesses self.record which requires a curve
    with pytest.raises(AttributeError):
        v.get_record()


def test_get_archive_definitions_appends_vertex_table(monkeypatch):
    monkeypatch.setattr(mod.Archivable, "get_archive_definitions", classmethod(lambda cls: []))

    defs = Vertex.get_archive_definitions()
    assert any(d.get("name") == "vertex" for d in defs)

    vertex_def = next(d for d in defs if d["name"] == "vertex")
    colnames = {c["name"] for c in vertex_def["columns"]}
    expected = {"curve_id", "curve_record_time", "id", "price", "quantity", "cost", "continuity", "uncertainty", "record"}
    assert expected <= colnames


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__]))