import abc
import logging

import tent.enumerations

from collections import deque
from typing import Mapping, Sequence

from sqlalchemy import Column, Enum, ForeignKey, ForeignKeyConstraint, Identity, Index, Table, types
from sqlalchemy.orm import registry

_log = logging.getLogger(__name__)

class DataManager:
    def __init__(self, parent, archival_buffer_length: int = 10000, *args, **kwargs):
        self.parent = parent

        # State Variables:
        self.archive_ready = False
        self.archival_queue = deque(maxlen=archival_buffer_length)
        self.definitions: dict[str, dict] = {}
        self.orm: dict = {}
        self.registry = registry()
        self.tables: dict[str, Table] = {}

    def add_table(self, key: str, definitions: list[dict]):
        definition = next(filter(lambda d: d['name'] == key, definitions))
        if key not in self.tables:
            self.definitions[key] = definition
            table = self._parse_table_definition(definition)
            self.tables[key] = table
        if key not in self.orm:
            parent_name = definition.get('inherits')
            parent_orm = self.orm.get(parent_name)
            if parent_name and not parent_orm:
                self.add_table(parent_name, definitions)
                parent_orm = self.orm.get(parent_name)
            orm_name = ''.join([word.title() for word in key.split('_')] + ['Table'])
            bases = (parent_orm,) if parent_orm else ()
            self.orm[key] = type(orm_name, bases, {})
            mapping_params = {'inherits': parent_orm, 'polymorphic_identity': key} if parent_orm else {}
            self.registry.map_imperatively(self.orm[key],
                                           self.tables[key],
                                           **mapping_params)

    def add_tables(self, definitions: list[dict]):
        for definition in definitions:
            key = definition['name']
            self.add_table(key, definitions)

    @staticmethod
    def _parse_column_def(column_def: dict):
        args = [column_def['name']]
        kwargs = column_def.get('kwargs', {})

        # Handle column type:
        col_type_name: str = column_def.get('type')
        if col_type_name:
            col_type_class: type = getattr(types, col_type_name, None)
            if col_type_class is None:
                raise ValueError(f'Unknown column type {col_type_name!r} in definition of column {column_def["name"]!r}.')
            col_type_args: list[str] = column_def.get('type_args')
            if col_type_class is types.DateTime:
                args.append(types.DateTime(timezone=True))  # TENT datetimes are always aware; stored as UTC
            elif col_type_class:
                args.append(col_type_class(*col_type_args) if col_type_args else col_type_class)

        # Handle enums:
        enumeration = column_def.get('enumeration')
        if enumeration:
            enum_obj = getattr(tent.enumerations, enumeration, None)
            if enum_obj is not None:
                args.append(Enum(enum_obj, create_type=True))

        # Handle foreign key reference:
        foreign_key = column_def.get('foreign_key')
        if foreign_key:
            args.append(ForeignKey(foreign_key))

        # Handle identity:
        identity = Identity() if column_def.get('identity') else None
        if identity:
            args.append(identity)

        return Column(*args, **kwargs)

    @staticmethod
    def _parse_index_def(index_def: dict):
        return Index(index_def['name'], *index_def['columns'], unique=index_def.get('unique', False))

    @staticmethod
    def _parse_constraint_def(constraint_def: dict):
        """Table-level constraints. Currently: {"foreign_key": {"columns": [...], "references": ["table.col", ...]}}."""
        if 'foreign_key' in constraint_def:
            fk = constraint_def['foreign_key']
            return ForeignKeyConstraint(fk['columns'], fk['references'], name=constraint_def.get('name'))
        raise ValueError(f'Unsupported table constraint definition: {constraint_def}')

    def _parse_table_definition(self, definition: dict):
        kwargs = definition.get('kwargs', {})
        name = definition['name']
        columns = [self._parse_column_def(c) for c in definition['columns']]
        indices = [self._parse_index_def(i) for i in definition.get('indices', [])]
        constraints = [self._parse_constraint_def(c) for c in definition.get('constraints', [])]
        return Table(name, self.registry.metadata, *columns, *indices, *constraints, **kwargs)

    @abc.abstractmethod
    def init_archive(self):
        pass

    @abc.abstractmethod
    def archive_data(self, records: Mapping|Sequence):
        pass

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        # This method is required by being a component, but is not used unless the Data Manager needs its own tables.
        pass
