import abc
import json
import logging

from tent.base.archivable import Archivable
from tent.config import ConfigModel, Configurable
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class Component(Configurable, Archivable, abc.ABC):
    """
    A named part of a TransactiveNode. Every component carries its validated configuration (``config``, an instance of
    its nested Config model; see tent.config) and archives each configuration version in the ``component_configuration``
    table: version 1 with its own row, and a new version each time reconfigure() changes it in place.
    """
    class Config(ConfigModel):
        """Markets compute their name and description from their series; the components that are configured by name
        add those fields in their own Config (see NamedConfig in tent.base.entity and the other bases)."""

    def __init__(self, description: str, name: str, transactive_node, config: ConfigModel | None = None):
        self._configure(config, {})
        super(Component, self).__init__()
        self.description: str = str(description)              # [text]
        self.name: str = str(name)                            # [text]
        self.tn = transactive_node
        self._archived_configuration_version = 0

    @property
    def data_manager(self):
        return self.tn.data_manager

    def archive(self, records: list[dict] = None):
        """
        Archive this component together with the headers of any Measurement it owns that has not been archived yet, and
        its configuration version when that has not been archived yet. Components create their Measurements in __init__,
        before their own row exists; writing the headers in the same unit of work keeps measurement.source_id valid
        under foreign-key enforcement (PostgreSQL).
        """
        from tent.containers.measurement import Measurement  # local import: containers import this module
        records = list(records) if records else []
        for value in list(vars(self).values()):
            if isinstance(value, Measurement) and not value.archived:
                records.append(value.archive_record())
        if self._archived_configuration_version < self.config_version:
            records.append(self.configuration_archive_record())
        super(Component, self).archive(records)

    def configuration_archive_record(self) -> dict:
        """The row of the component_configuration table for the current configuration version."""
        self._archived_configuration_version = self.config_version
        return {'table_name': 'component_configuration',
                'data': {'component_id': self.id, 'version': self.config_version, 'record_time': Timer.now(),
                         'class_name': type(self).__name__,
                         'configuration': json.dumps(self.configuration_record(), sort_keys=True)}}

    def configuration_record(self) -> dict:
        # Objects given in code (components wired directly, adapters) are written by their repr: the archive keeps the
        # configuration as data, and such objects have no configuration form.
        return self.config.model_dump(mode='json', fallback=repr)

    def _configuration_changed(self, changed: set[str]) -> None:
        """A reconfiguration was applied: the new configuration version is archived."""
        try:
            self.data_manager.archive_data([self.configuration_archive_record()])
        except Exception:
            _log.exception(f'Failed to archive configuration version {self.config_version} of {self.table_name} '
                           f'{self.name!r} (id={self.id}).')

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
            },
            {
                # Append-only: one row per configuration version of a component (1 at construction, +1 per in-place
                # reconfiguration). The configuration is the component's validated Config as JSON.
                "name": "component_configuration",
                "columns": [
                    {"name": "component_id", "type": "Uuid", "foreign_key": "component.id",
                     "kwargs": {"primary_key": True}},
                    {"name": "version", "type": "Integer", "kwargs": {"primary_key": True}},
                    {"name": "record_time", "type": "DateTime"},
                    {"name": "class_name", "type": "String", "type_args": [255]},
                    {"name": "configuration", "type": "Text"}
                ]
            }
        ])
        return definitions
