import abc

from datetime import datetime, timezone
from uuid6 import uuid7, UUID
from weakref import ref, WeakSet


class Archivable:
    def __init__(self):
        self._id: UUID = uuid7()
        self._real_time_created: datetime = datetime.now(timezone.utc).astimezone()

    @property
    def id(self) -> UUID:
        return self._id

    @property
    def real_time_created(self) -> datetime:
        return self._real_time_created

    @property
    @abc.abstractmethod
    def data_manager(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def table_name(self):
        raise NotImplementedError

    def archive(self, records: list[dict] = None):
        records = records if records else []
        records.extend([
                {
                    'table_name': self.table_name,
                    'data': self.get_record()
                }
            ])
        self.data_manager.archive_data(records)

    @abc.abstractmethod
    def get_record(self) -> dict:
        return {
            "id": self.id
        }

    @classmethod
    @abc.abstractmethod
    def get_archive_definitions(cls) -> list[dict]:
        return []

class Component(Archivable):
    def __init__(self, description: str, name: str, transactive_node):
        super(Component, self).__init__()
        self.description: str = str(description)              # [text]
        self.name: str = str(name)                            # [text]
        self.tn = ref(transactive_node)

    @property
    def data_manager(self):
        return self.tn().data_manager

    @abc.abstractmethod
    def get_record(self) -> dict:
        record = super(Component, self).get_record()
        record.update({
            'name': self.name,
            'description': self.description,
            'tn_id': self.tn().id
        })
        return record

    @classmethod
    @abc.abstractmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Component, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "component",
                "columns": [
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "name", "type": "String", "type_args": [255]},
                    {"name": "tn_id", "foreign_key": "transactive_node.id"},
                    {"name": "description", "type": "String", "type_args": [255]}
                ]
            }
        ])
        return definitions

# Vertex cannot be imported before Archivable. (This would create a circular dependency.)
from tent.containers import Vertex


class Entity(Component):
    def __init__(self, cost_parameters: list[float], default_vertices: list[float], description: str,
                 information_services: list, location: str, maximum_power: float, meter_points: list,
                 minimum_power: float, name: str, transactive_node):
        super(Entity, self).__init__(description, name, transactive_node)
        self.cost_parameters: list[float] = [float(cp) for cp in cost_parameters]  # [float, float, float] Coefficients of quadratic cost function
        self.location = str(location)                    # [text]
        self.maximum_power = float(maximum_power)           # [avg.kW, signed] Asset's physical "hard" constraint
        self.minimum_power = float(minimum_power)           # [avg.kW, signed] Asset's physical "hard" constraint

        # [Vertex] Default vertices; default supply/demand curve
        self.default_vertices = [Vertex(float("inf"), 0.0, 1)] if default_vertices is None \
            else Vertex.vertex_list(default_vertices)

        # [InformationService] See class InformationService
        self.information_services = WeakSet()
        information_services = information_services if information_services is not None else []
        if all([isinstance(ism, Component) for ism in information_services]):
            self.information_services.update(information_services)
        elif all([isinstance(ism, str) for ism in information_services]) and self.tn and self.tn():
            self.information_services.update(self.tn().get_isms_by_attribute('name', information_services))
        else:
            raise ValueError(f'All information services assigned to LocalAsset {self.name} must be an instance of class'
                             f' InformationServiceModel or the name of an InformationServiceModel held by the'
                             f' TransactiveNode.')

        # [MeterPoint] See class MeterPoint
        self.meter_points = WeakSet()
        meter_points = meter_points if meter_points is not None else []
        if all([isinstance(mp, Component) for mp in meter_points]):
            self.meter_points.update(meter_points)
        elif all([isinstance(mp, str) for mp in meter_points]) and self.tn and self.tn():
            self.meter_points.update(self.tn().get_meters_by_attribute('name', meter_points))
        else:
            raise ValueError(f'All meter points assigned to LocalAsset {self.name} must be an instance of class'
                             f' MeterPoint or the name of a MeterPoint held by the TransactiveNode.')

    @abc.abstractmethod
    def get_record(self) -> dict:
        record = super(Entity, self).get_record()
        record.update({
            'cost_parameters_0': self.cost_parameters[0],
            'cost_parameters_1': self.cost_parameters[1],
            'cost_parameters_2': self.cost_parameters[2],
            #'default_vertices': self.default_vertices,
            'location': self.location,
            'maximum_power': self.maximum_power,
            'minimum_power': self.minimum_power
        })
        return record

    def archive(self, records: list[dict] = None):
        records = records if records else []
        records.extend([
            {'table_name': 'entity_meter_point', 'data': {'component': self.id, 'meter_point_id': m.id}}
            for m in self.meter_points
        ])
        records.extend([
            {'table_name': 'entity_information_service', 'data': {'component': self.id, 'information_service_id': i.id}}
            for i in self.information_services
        ])
        super(Entity, self).archive(records)

    @classmethod
    @abc.abstractmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Entity, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "entity",
                "inherits": "component",
                "columns": [
                    {"name": "id", "type": "Uuid", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "cost_parameters_0", "type": "Float"},
                    {"name": "cost_parameters_1", "type": "Float"},
                    {"name": "cost_parameters_2", "type": "Float"},
                    #{"name": "default_vertices", "foreign_key": "sd_curve.id"},
                    {"name": "location", "type": "String", "type_args": [255]},
                    {"name": "maximum_power", "type": "Float"},
                    {"name": "minimum_power", "type": "Float"}
                ]
            }
        ])
        return definitions