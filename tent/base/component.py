import abc

from tent.base.archivable import Archivable

class Component(Archivable):
    def __init__(self, description: str, name: str, transactive_node):
        super(Component, self).__init__()
        self.description: str = str(description)              # [text]
        self.name: str = str(name)                            # [text]
        self.tn = transactive_node

    @property
    def data_manager(self):
        return self.tn.data_manager

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
