import json
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typing import Mapping, Sequence

from tent.data_manager import DataManager

_log = logging.getLogger(__name__)


class LocalDataManager(DataManager):
    def __init__(self, parent, db_params: dict, *args, **kwargs):
        super(LocalDataManager, self).__init__(parent, *args, **kwargs)
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
                # Early return so we don't attempt to call a NoneType (regression fix for unit test failure)
                return
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


# TODO: Move to TransactiveNodeAgent.
class VolttronDataManager(DataManager):
    def __init__(self, parent, historian_topic: str = 'TNS/Archive', historian_vip: str = 'tns.historian'):
        # NOTE: Parent is assumed to be a VOLTTRON Agent.
        super(VolttronDataManager, self).__init__(parent)
        # Store reference used by tests and internal methods
        self.historian_topic = historian_topic
        self.historian_vip = historian_vip

    def init_archive(self):
        # TODO: Should there be a key validation in header to create tables?
        # TODO: Should this really be using RPC?
        # TODO: Should it instead expose an RPC to allow the historian to collect tables?
        message = json.dumps(self.definitions)
        self.parent.rpc.call(self.historian_vip, 'create_tables', message)

    def archive_data(self, records: Mapping|Sequence):
        # TODO: This should use self.parent.vip.pubsub.publish directly.  Does anything need changed?
        # TODO: This should use self.parent.vip.pubsub.publish directly.  Does anything need changed?
        self.parent.publish(self.historian_topic, message=records)


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




