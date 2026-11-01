"""
Archive schema versioning: the version constant, the fingerprint that ties it to the shipped definitions, the
archive_schema row a new database gets, and the refusal of a database written by another version.
"""
import logging
import random
from datetime import timezone

import pytest
from sqlalchemy import text

import tent.data_manager
import tent.data_manager.reader
from tent.data_manager import (ARCHIVE_SCHEMA_TABLE, ARCHIVE_SCHEMA_VERSION, ArchiveSchemaMismatch, DataManager,
                               archive_schema_definition, archive_schema_fingerprint, canonical_definitions_json)
from tent.data_manager.local_data_manager import LocalDataManager
from tent.data_manager.reader import known_archive_definitions

# The fingerprint of each archive schema version's shipped definitions. When test_version_matches_the_definitions
# fails, a shipped table definition changed: increment ARCHIVE_SCHEMA_VERSION in tent/data_manager/__init__.py, add
# the new version with the fingerprint the failure prints here, and keep the old entries. Version 1 (every archive
# before 2 November 2026) predates the fingerprint and has none.
RECORDED_FINGERPRINTS = {
    2: '965cdb9a0ef4fdb8cd7e025828cbc33493d61e511883c9dbc723fd6690fa9f9e',
}


def open_archive(url):
    """A writable data manager with every shipped table registered, as a configured node leaves it."""
    dm = LocalDataManager(None, {'url': url})
    dm.add_tables(known_archive_definitions())
    return dm


# ---------------------------------------------------------------- the constant and the fingerprint
def test_version_is_an_integer_from_two_up():
    assert isinstance(ARCHIVE_SCHEMA_VERSION, int) and not isinstance(ARCHIVE_SCHEMA_VERSION, bool)
    assert ARCHIVE_SCHEMA_VERSION >= 2, 'version 1 is the unversioned past; the first versioned schema is 2'


def test_version_matches_the_definitions():
    fingerprint = archive_schema_fingerprint()
    assert ARCHIVE_SCHEMA_VERSION in RECORDED_FINGERPRINTS, (
        f'ARCHIVE_SCHEMA_VERSION is {ARCHIVE_SCHEMA_VERSION} but RECORDED_FINGERPRINTS has no entry for it; add '
        f'{ARCHIVE_SCHEMA_VERSION}: {fingerprint!r}.')
    assert fingerprint == RECORDED_FINGERPRINTS[ARCHIVE_SCHEMA_VERSION], (
        f'The shipped table definitions changed but ARCHIVE_SCHEMA_VERSION is still {ARCHIVE_SCHEMA_VERSION}. '
        f'Increment ARCHIVE_SCHEMA_VERSION in tent/data_manager/__init__.py and record the new fingerprint '
        f'{fingerprint!r} under the new version in RECORDED_FINGERPRINTS (keep the old entries).')


def test_fingerprint_covers_every_shipped_table_including_archive_schema():
    names = {d['name'] for d in known_archive_definitions()}
    assert ARCHIVE_SCHEMA_TABLE in names
    assert {'transactive_node', 'component', 'market', 'measurement_value', 'sd_curve', 'meter_reading'} <= names


def test_fingerprint_does_not_depend_on_table_or_column_order():
    definitions = known_archive_definitions()
    shuffled = [dict(d, columns=random.sample(d['columns'], len(d['columns']))) for d in definitions]
    random.shuffle(shuffled)
    assert canonical_definitions_json(shuffled) == canonical_definitions_json(definitions)


def test_fingerprint_changes_when_a_definition_changes(monkeypatch):
    before = archive_schema_fingerprint()
    changed = [dict(d) for d in known_archive_definitions()]
    changed[0] = dict(changed[0], columns=changed[0]['columns'] + [{'name': 'extra', 'type': 'Integer'}])
    monkeypatch.setattr(tent.data_manager.reader, 'known_archive_definitions', lambda: changed)
    assert archive_schema_fingerprint() != before


# ---------------------------------------------------------------- the row in the database
def test_new_database_gets_the_archive_schema_row(tmp_path):
    dm = open_archive(f'sqlite:///{tmp_path / "new.sqlite"}')
    dm.init_archive()
    record = dm.archive_schema_record()
    assert record['version'] == ARCHIVE_SCHEMA_VERSION == dm.schema_version
    assert record['fingerprint'] == archive_schema_fingerprint()
    assert record['tent_version'] and record['created'].tzinfo == timezone.utc
    assert dm.has_table('transactive_node'), 'the node tables are created with the row'


def test_reopening_with_the_same_version_keeps_one_row(tmp_path):
    url = f'sqlite:///{tmp_path / "same.sqlite"}'
    open_archive(url).init_archive()
    dm = open_archive(url)
    dm.init_archive()  # a restart
    dm.init_archive()  # more tables registered during a run
    assert dm.execute(dm.select(ARCHIVE_SCHEMA_TABLE)) == [dm.archive_schema_record()]


def test_database_of_another_version_is_refused_naming_both_versions(tmp_path):
    url = f'sqlite:///{tmp_path / "other.sqlite"}'
    dm = open_archive(url)
    dm.init_archive()
    with dm.engine.begin() as connection:
        connection.execute(text(f'UPDATE {ARCHIVE_SCHEMA_TABLE} SET version = 7'))
    with pytest.raises(ArchiveSchemaMismatch) as error:
        open_archive(url).init_archive()
    message = str(error.value)
    assert 'version 7' in message and f'version {ARCHIVE_SCHEMA_VERSION}' in message
    assert 'Recreate the database' in message and 'read-only' in message
    assert error.value.database_version == 7 and error.value.tent_schema_version == ARCHIVE_SCHEMA_VERSION


def test_database_without_the_table_is_version_one_and_refused(tmp_path):
    url = f'sqlite:///{tmp_path / "old.sqlite"}'
    dm = open_archive(url)
    dm.init_archive()
    with dm.engine.begin() as connection:
        connection.execute(text(f'DROP TABLE {ARCHIVE_SCHEMA_TABLE}'))  # what an archive from before 2 Nov 2026 looks like
    with pytest.raises(ArchiveSchemaMismatch, match=f'version 1;.*version {ARCHIVE_SCHEMA_VERSION}'):
        open_archive(url).init_archive()


def test_same_version_with_another_fingerprint_is_a_warning(tmp_path, caplog):
    url = f'sqlite:///{tmp_path / "fp.sqlite"}'
    dm = open_archive(url)
    dm.init_archive()
    with dm.engine.begin() as connection:
        connection.execute(text(f"UPDATE {ARCHIVE_SCHEMA_TABLE} SET fingerprint = 'abc'"))
    with caplog.at_level(logging.WARNING, logger='tent.data_manager'):
        open_archive(url).init_archive()
    assert 'without a version bump' in caplog.text and 'abc' in caplog.text


def test_tables_tent_does_not_know_do_not_make_a_database_old(tmp_path):
    """A shared database that holds something else's tables, but none of this manager's, is a new archive."""
    url = f'sqlite:///{tmp_path / "shared.sqlite"}'
    dm = open_archive(url)
    with dm.engine.begin() as connection:
        connection.execute(text('CREATE TABLE somebody_elses (id INTEGER PRIMARY KEY)'))
    dm.init_archive()
    assert dm.schema_version == ARCHIVE_SCHEMA_VERSION


def test_check_hook_for_other_data_managers():
    """Any DataManager can check a row it obtained elsewhere (a historian's, for example) with the base-class hook."""
    DataManager.check_archive_schema(DataManager.archive_schema_row())
    with pytest.raises(ArchiveSchemaMismatch, match='version 1'):
        DataManager.check_archive_schema(None, where='Historian database')
    with pytest.raises(ArchiveSchemaMismatch, match='Historian database was written with archive schema version 3'):
        DataManager.check_archive_schema({'version': 3, 'fingerprint': 'x'}, where='Historian database')


# ---------------------------------------------------------------- the reader
def test_reader_reports_the_version_and_none_without_the_table(tmp_path, caplog):
    url = f'sqlite:///{tmp_path / "read.sqlite"}'
    open_archive(url).init_archive()
    reader = LocalDataManager.from_url(url)
    assert reader.schema_version == ARCHIVE_SCHEMA_VERSION
    assert reader.archive_schema_record()['fingerprint'] == archive_schema_fingerprint()
    with pytest.raises(RuntimeError, match='opened for reading'):
        reader.init_archive()

    with reader.engine.begin() as connection:
        connection.execute(text(f'DROP TABLE {ARCHIVE_SCHEMA_TABLE}'))
    with caplog.at_level(logging.WARNING, logger='tent.data_manager.reader'):
        old = LocalDataManager.from_url(url)
    assert old.schema_version is None and old.archive_schema_record() is None
    assert 'archive schema version 1' in caplog.text

    assert LocalDataManager.from_url(f'sqlite:///{tmp_path / "empty.sqlite"}').schema_version is None


def test_archive_schema_definition_is_parseable_by_the_data_manager():
    dm = LocalDataManager(None, {'url': 'sqlite://'})
    dm.add_tables([archive_schema_definition()])
    assert {c.name for c in dm.tables[ARCHIVE_SCHEMA_TABLE].columns} == {'version', 'fingerprint', 'tent_version',
                                                                           'created'}
