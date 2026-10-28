import abc

from tent.base.archivable import Archivable

class Component(Archivable, abc.ABC):
    def __init__(self, description: str, name: str, transactive_node):
        super(Component, self).__init__()
        self.description: str = str(description)              # [text]
        self.name: str = str(name)                            # [text]
        self.tn = transactive_node

    @property
    def data_manager(self):
        return self.tn.data_manager

    def archive(self, records: list[dict] = None):
        """
        Archive this component together with the headers of any Measurement it owns that has not been archived yet.
        Components create their Measurements in __init__, before their own row exists; writing the headers in the same
        unit of work keeps measurement.source_id valid under foreign-key enforcement (PostgreSQL).
        """
        from tent.containers.measurement import Measurement  # local import: containers import this module
        records = list(records) if records else []
        for value in list(vars(self).values()):
            if isinstance(value, Measurement) and not value.archived:
                records.append(value.archive_record())
        super(Component, self).archive(records)

    @abc.abstractmethod
    def get_record(self) -> dict:
        record = super(Component, self).get_record()
        record.update({
            'name': self.name,
            'description': self.description,
            'tn_id': self.tn.id
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
