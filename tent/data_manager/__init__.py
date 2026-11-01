import abc
import hashlib
import importlib.metadata
import json
import logging

import tent.enumerations

from collections import deque
from datetime import datetime, timezone
from typing import Mapping, Sequence

from sqlalchemy import Column, Enum, ForeignKey, ForeignKeyConstraint, Identity, Index, Table, types
from sqlalchemy.orm import registry

from tent.config import ConfigModel, Configurable, Setting

_log = logging.getLogger(__name__)

# The version of the set of table definitions TENT ships (known_archive_definitions()). It is incremented whenever any
# shipped definition changes, so that a database says which TENT wrote it: the historian refuses a database or a node
# of another version, and tent-analysis reports what it is reading. Version 1 is every archive written before
# 2 November 2026; those databases have no archive_schema table. Version 2 (2 November 2026) is the first to carry the
# table. The fingerprint of each version's definitions is recorded in
# tests/unit_tests/data_manager_tests/test_archive_schema.py; that test fails when a definition changes without a bump.
ARCHIVE_SCHEMA_VERSION = 2
ARCHIVE_SCHEMA_TABLE = 'archive_schema'


def archive_schema_definition() -> dict:
    """The table that records which archive schema a database was written with: one row, written when the database is
    created. Shipped like any other definition (it is in known_archive_definitions()) so a reader decodes it."""
    return {
        'name': ARCHIVE_SCHEMA_TABLE,
        'columns': [
            {'name': 'version', 'type': 'Integer', 'kwargs': {'primary_key': True}},
            {'name': 'fingerprint', 'type': 'String', 'type_args': [64]},
            {'name': 'tent_version', 'type': 'String', 'type_args': [32]},
            {'name': 'created', 'type': 'DateTime'},
        ],
    }


def canonical_definitions_json(definitions: Sequence[Mapping]) -> str:
    """The definitions as one JSON document in which nothing depends on order: tables sorted by name, columns sorted
    by name, keys sorted. Column order is not part of a definition (DataManager.definition_differences)."""
    canonical = []
    for definition in sorted(definitions, key=lambda d: d['name']):
        definition = dict(definition)
        definition['columns'] = sorted(definition.get('columns', []), key=lambda c: c['name'])
        canonical.append(definition)
    return json.dumps(canonical, sort_keys=True, separators=(',', ':'))


def archive_schema_fingerprint() -> str:
    """SHA-256 of the canonical JSON of every shipped table definition. Two TENTs with the same fingerprint write the
    same schema; a fingerprint that changes while ARCHIVE_SCHEMA_VERSION does not is a definition change without a
    version bump, which the recorded-fingerprint test catches."""
    from tent.data_manager.reader import known_archive_definitions  # the reader imports the component modules lazily
    return hashlib.sha256(canonical_definitions_json(known_archive_definitions()).encode()).hexdigest()


def tent_version() -> str:
    """The installed TENT distribution's version, or 'unknown' when TENT runs from a source tree that is not installed."""
    try:
        return importlib.metadata.version('tent')
    except importlib.metadata.PackageNotFoundError:
        return 'unknown'


class ArchiveSchemaMismatch(RuntimeError):
    """An existing database was written with another archive schema version than this TENT writes."""

    def __init__(self, database_version: int, tent_schema_version: int, where: str = 'The database'):
        self.database_version = database_version
        self.tent_schema_version = tent_schema_version
        super().__init__(
            f'{where} was written with archive schema version {database_version}; this TENT writes archive schema '
            f'version {tent_schema_version}. Nothing is migrated. Recreate the database (point the data manager at a '
            f'new URL or delete the file) or open it read-only with a TENT whose archive schema version is '
            f'{database_version}.')


class DataManager(Configurable):
    """Where a node's archive records go. Configured in the node's ``data_manager`` block (class_name and the settings
    of the class's Config); the node passes itself as `parent`."""
    class Config(ConfigModel):
        archival_buffer_length: int = Setting(10000, ge=1,
                                              description='Number of queued records above which a warning is logged '
                                                          'while the archive is not ready.')

    def __init__(self, parent, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        self.parent = parent

        # State Variables:
        self.archive_ready = False
        # Unbounded: a bounded deque silently dropped the oldest records. The length is a warning threshold instead.
        self.archival_queue = deque()
        self.archival_buffer_length = int(cfg.archival_buffer_length)
        self._queue_warned = False
        self.definitions: dict[str, dict] = {}
        self.orm: dict = {}
        self.registry = registry()
        self.tables: dict[str, Table] = {}

    def add_table(self, key: str, definitions: list[dict]):
        definition = next(filter(lambda d: d['name'] == key, definitions))
        if key in self.tables:
            # Every component class registers the tables it shares with its ancestors (component, entity, ...), so the
            # same definition arrives many times. A different definition under a known name would silently archive
            # into the first table registered; it is refused instead.
            differences = self.definition_differences(self.definitions[key], definition)
            if differences:
                raise ValueError(f'Table {key!r} is already registered with a different definition: '
                                 f'{"; ".join(differences)}')
        else:
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

    @staticmethod
    def definition_differences(existing: dict, proposed: dict) -> list[str]:
        """Human-readable differences between two table definitions of the same name; empty when they agree. Column
        order is not part of a definition (columns are compared by name); everything else is."""
        differences = []
        def by_name(definition):
            return {column['name']: column for column in definition.get('columns', [])}
        existing_columns, proposed_columns = by_name(existing), by_name(proposed)
        for name in sorted(set(existing_columns) - set(proposed_columns)):
            differences.append(f'column {name!r} is missing from the new definition')
        for name in sorted(set(proposed_columns) - set(existing_columns)):
            differences.append(f'column {name!r} is new')
        for name in sorted(set(existing_columns) & set(proposed_columns)):
            if existing_columns[name] != proposed_columns[name]:
                differences.append(f'column {name!r} differs: {existing_columns[name]} versus {proposed_columns[name]}')
        for field in sorted((set(existing) | set(proposed)) - {'columns', 'name'}):
            if existing.get(field) != proposed.get(field):
                differences.append(f'{field!r} differs: {existing.get(field)!r} versus {proposed.get(field)!r}')
        return differences

    def stop(self) -> None:
        """Release what the manager holds before the node is torn down (a manager that publishes or buffers overrides
        this to flush and stop its workers). The base manager holds nothing."""
        return None

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
            if enum_obj is None:
                raise ValueError(f'Unknown enumeration {enumeration!r} in definition of column {column_def["name"]!r}; '
                                 f'it must be a member of tent.enumerations.')
            args.append(Enum(enum_obj, create_type=True))

        # Handle foreign key reference:
        foreign_key = column_def.get('foreign_key')
        if foreign_key:
            args.append(ForeignKey(foreign_key))

        # Handle identity: an SQL identity clause is only valid on integer columns (PostgreSQL rejects it elsewhere).
        # UUID primary keys are generated by Archivable (uuid7), so the flag is ignored for them.
        if column_def.get('identity') and col_type_name in ('Integer', 'BigInteger', 'SmallInteger'):
            args.append(Identity())

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

    # ---------------------------------------------------------------- archive schema version
    @staticmethod
    def archive_schema_row() -> dict:
        """The archive_schema row this TENT writes into a new database: the version constant, the fingerprint of the
        shipped definitions, the installed TENT version and the time of creation (UTC)."""
        return {'version': ARCHIVE_SCHEMA_VERSION, 'fingerprint': archive_schema_fingerprint(),
                'tent_version': tent_version(), 'created': datetime.now(timezone.utc)}

    @staticmethod
    def check_archive_schema(stored: Mapping | None, where: str = 'The database') -> None:
        """
        Compare the archive_schema row an existing database holds with what this TENT writes. `stored` is the row, or
        None for a database that predates the table (archive schema version 1). A different version raises
        ArchiveSchemaMismatch naming both versions; the same version with another fingerprint is a definition change
        that was never given a version and is logged as a warning (the archive may still disagree with the code).
        Managers that own a database (or reach one, through a historian) call this before they write to it.
        """
        database_version = 1 if stored is None else int(stored['version'])
        if database_version != ARCHIVE_SCHEMA_VERSION:
            raise ArchiveSchemaMismatch(database_version, ARCHIVE_SCHEMA_VERSION, where)
        fingerprint = archive_schema_fingerprint()
        if stored is not None and stored.get('fingerprint') != fingerprint:
            _log.warning(f'{where} records archive schema version {database_version} with fingerprint '
                         f'{stored.get("fingerprint")}, but this TENT\'s version {ARCHIVE_SCHEMA_VERSION} definitions '
                         f'have fingerprint {fingerprint}: a table definition changed without a version bump. The '
                         f'tables in the database may not match what TENT writes.')

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
