import abc
import json
import logging
import sqlalchemy_utils
import tent.enumerations

from collections import deque
from sqlalchemy import Column, create_engine, Enum, ForeignKey, Identity, Index, Table, types
from sqlalchemy.orm import registry, Session
from typing import Mapping, Sequence

from tent.utils.log import setup_logging

setup_logging()
_log = logging.getLogger(__name__)

class DataManager:
    def __init__(self, transactive_node, archival_buffer_length: int = 100, *args, **kwargs):
        self.tn = transactive_node

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
            col_type_class: type = getattr(types, col_type_name, getattr(sqlalchemy_utils, col_type_name, None))
            col_type_args: list[str] = column_def.get('type_args')
            if col_type_class:
                args.append(col_type_class(*col_type_args) if col_type_args else col_type_class)

        # Handle enums:
        enumeration = column_def.get('enumeration')
        if enumeration:
            args.append(Enum(getattr(tent.enumerations, enumeration), create_type=True))

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

    def _parse_table_definition(self, definition: dict):
        kwargs = definition.get('kwargs', {})
        name = definition['name']
        columns = [self._parse_column_def(c) for c in definition['columns']]
        indices = [self._parse_index_def(i) for i in definition.get('indices', [])]
        return Table(name, self.registry.metadata, *columns, *indices, **kwargs)

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

class LocalDataManager(DataManager):
    def __init__(self, transactive_node, db_params: dict, *args, **kwargs):
        super(LocalDataManager, self).__init__(transactive_node, *args, **kwargs)
        # TODO: Should this just be checking for a db_type of local? Is this the same code for postgres or others?
        self.db_params = db_params if db_params else {}
        database_url = self.db_params.get('url', 'sqlite:///tent_db.sqlite')
        self.engine = create_engine(database_url)

    def init_archive(self):
        self.registry.metadata.create_all(self.engine, checkfirst=True)
        # TODO: Can we check if the database is actually successfully created and ready here?
        self.archive_ready = True
        self.archive_data()

    def archive_data(self, records: Mapping|Sequence = None):
        # TODO: Would it be better to move db write logic into a periodic and always append here to the archival_queue?
        records = records if records else []
        if not self.archive_ready:
            self.archival_queue.extend(records)
            return
        def _add_to_session(sess, rec):
            table_name = rec['table_name']
            table_orm = self.orm.get(table_name)
            if not table_orm:
                _log.warning(f'Attempted to archive data to non-existent table: {table_name}')
            _log.debug(rec['data'])
            record_object = table_orm(**rec['data'])
            sess.add(record_object)

        with Session(self.engine) as session:
            while self.archival_queue:
                _add_to_session(session, self.archival_queue.pop())
            records = [records] if isinstance(records, Mapping) else records
            for record in records:
                _add_to_session(session, record)
            # TODO: Error handling, if this fails we should end up with everything still in the archival_queue.
            session.commit()

class VolttronDataManager(DataManager):
    def __init__(self, transactive_node, historian_topic: str = 'TNS/Archive', historian_vip: str = 'tns.historian'):
        super(VolttronDataManager, self).__init__(transactive_node)
        self.historian_topic = historian_topic
        self.historian_vip = historian_vip

    def init_archive(self):
        # TODO: Should there be a key validation in header to create tables?
        # TODO: Should this really be using RPC?
        # TODO: Should it instead expose an RPC to allow the historian to collect tables?
        message = json.dumps(self.definitions)
        self.tn.rpc.call(self.historian_vip, 'create_tables', message)

    def archive_data(self, records: Mapping|Sequence):
        self.tn.publish(self.historian_topic, message=records)


# # TODO: This should end up elsewhere, but for now, a record of how to initialize a local db:
# def make_local_db():
#     from tent.utils.data_manager_new import LocalDataManager
#     from tent.utils.database_defs import all_tables
#     from tent.transactive_node import TransactiveNode
#     tn = TransactiveNode()
#     dm = LocalDataManager(tn, db_params={'db_type': 'sqlite', 'url': 'sqlite:///testent.sqlite'})
#     definitions = all_tables()
#     dm.add_tables(definitions)
#     dm.init_archive()
#     return dm




