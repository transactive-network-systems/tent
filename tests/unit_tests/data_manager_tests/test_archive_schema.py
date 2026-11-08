"""
TENT's archive schema version: the version constant, the fingerprint that ties it to the shipped definitions and
enumerations, the ArchiveSchema TENT declares on its data managers, and what a TENT-prepared archive does with a
database of another version. The mechanics (the row, the refusal, the warning) are the library's and tested there.
"""
import logging
import random

import pytest
from sqlalchemy import text

from archivable import ARCHIVE_SCHEMA_TABLE, ArchiveSchemaMismatch, LocalDataManager, canonical_definitions_json, \
    enumeration_definitions

import tent.data_manager
import tent.enumerations
from tent.data_manager import (APPLICATION, ARCHIVE_SCHEMA_VERSION, archive_schema, archive_schema_fingerprint,
                               known_archive_definitions, known_enumerations, prepare, tent_version)
from tent.data_manager.reader import open_archive

# The fingerprint of each archive schema version's shipped definitions. When test_version_matches_the_definitions
# fails, a shipped table definition or enumeration changed: increment ARCHIVE_SCHEMA_VERSION in
# tent/data_manager/__init__.py, add the new version with the fingerprint the failure prints here, and keep the old
# entries. Version 1 (every archive before 2 November 2026) predates the fingerprint and has none.
RECORDED_FINGERPRINTS = {
    2: 'dd03e0ba4449f15391b5a84339639dd7284d113afd54bb75ba193fcd5aa10979',  # 2 November 2026: every shipped class (ResponseNeighbor included); the fingerprint of this version covered the table definitions only, with TENT's own archive_schema table (version, fingerprint, tent_version, created)
    3: '78f0819d2817fc99b8f490b2b516cb0ebe076a9732535b40bbefaa4b89103cf0',  # 2 November 2026: the library's archive_schema table, one row per application (application primary key, version, fingerprint, application_version, created); the fingerprint covers the enumerations too; the market row has actuate_assets
    4: '08e1d982f1cf7036455cf7e65e529bda572c943d8661002a53195585121bdc14',  # 2 November 2026: the matching_auction table (extends auction with matching_strategy) and the MarketTypes members matching_auction and matching_consensus
    5: 'e56233b5dd28a649e697135ccf4a47910b5a1e99f68501e3a6399ec8a3608134',  # 2 November 2026: the matching_consensus table (MatchingConsensusMarket, the consensus protocol with an order book as the balancing step), extending market with matching_strategy
    6: '0a5accad658e41c0f0b1f3d5cd63d88792b39818f63a875cf0f782a25140f734',  # 8 November 2026: sd_curve.balance_point_record (nullable Integer), the record number of a signal curve's balance point among the vertex rows of that curve version, the exchange the signal proposes; NULL for other curves
}


def open_writable(url):
    """A writable data manager with every shipped table registered, as a configured node leaves it."""
    dm = prepare(LocalDataManager(None, {'url': url}))
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
        f'The shipped table definitions or enumerations changed but ARCHIVE_SCHEMA_VERSION is still '
        f'{ARCHIVE_SCHEMA_VERSION}. Increment ARCHIVE_SCHEMA_VERSION in tent/data_manager/__init__.py and record the '
        f'new fingerprint {fingerprint!r} under the new version in RECORDED_FINGERPRINTS (keep the old entries).')


def test_fingerprint_covers_every_shipped_table_and_enumeration():
    names = {d['name'] for d in known_archive_definitions()}
    assert ARCHIVE_SCHEMA_TABLE in names
    assert {'transactive_node', 'component', 'market', 'measurement_value', 'sd_curve', 'meter_reading'} <= names
    assert [e.__name__ for e in known_enumerations()] == list(tent.enumerations.__all__)
    named_in_columns = {c['enumeration'] for d in known_archive_definitions() for c in d['columns'] if 'enumeration' in c}
    assert named_in_columns <= {e.__name__ for e in known_enumerations()}
    assert 'MarketState' in named_in_columns


def test_fingerprint_does_not_depend_on_table_or_column_order():
    definitions = known_archive_definitions()
    enumerations = enumeration_definitions(known_enumerations())
    shuffled = [dict(d, columns=random.sample(d['columns'], len(d['columns']))) for d in definitions]
    random.shuffle(shuffled)
    assert canonical_definitions_json(shuffled, list(reversed(enumerations))) == \
        canonical_definitions_json(definitions, enumerations)


def test_fingerprint_changes_when_a_definition_or_an_enumeration_changes(monkeypatch):
    before = archive_schema_fingerprint()
    changed = [dict(d) for d in known_archive_definitions()]
    changed[0] = dict(changed[0], columns=changed[0]['columns'] + [{'name': 'extra', 'type': 'Integer'}])
    monkeypatch.setattr(tent.data_manager, 'known_archive_definitions', lambda: changed)
    assert archive_schema_fingerprint() != before
    monkeypatch.undo()
    monkeypatch.setattr(tent.data_manager, 'known_enumerations', lambda: known_enumerations()[1:])
    assert archive_schema_fingerprint() != before


def test_the_declared_schema_names_tent_and_its_version():
    schema = archive_schema()
    assert (schema.application, schema.version) == (APPLICATION, ARCHIVE_SCHEMA_VERSION) == ('TENT', ARCHIVE_SCHEMA_VERSION)
    assert schema.fingerprint == archive_schema_fingerprint() and schema.application_version == tent_version()
    assert tent_version() != 'unknown', 'the test environment installs TENT'


# ---------------------------------------------------------------- a TENT-prepared archive
def test_new_database_gets_the_row_and_the_node_tables(tmp_path):
    dm = open_writable(f'sqlite:///{tmp_path / "new.sqlite"}')
    dm.init_archive()
    record = dm.archive_schema_record()
    assert record['version'] == ARCHIVE_SCHEMA_VERSION == dm.schema_version
    assert record['fingerprint'] == archive_schema_fingerprint()
    assert (record['application'], record['application_version']) == ('TENT', tent_version())
    assert dm.has_table('transactive_node') and dm.has_table('market_state_change')


def test_database_of_another_version_is_refused_naming_tent_and_both_versions(tmp_path):
    url = f'sqlite:///{tmp_path / "other.sqlite"}'
    dm = open_writable(url)
    dm.init_archive()
    with dm.engine.begin() as connection:
        connection.execute(text(f'UPDATE {ARCHIVE_SCHEMA_TABLE} SET version = 7'))
    with pytest.raises(ArchiveSchemaMismatch) as error:
        open_writable(url).init_archive()
    message = str(error.value)
    assert 'version 7' in message and f'; TENT writes archive schema version {ARCHIVE_SCHEMA_VERSION}' in message
    assert error.value.database_version == 7 and error.value.expected_version == ARCHIVE_SCHEMA_VERSION


def test_database_without_the_table_is_version_one_and_refused(tmp_path):
    url = f'sqlite:///{tmp_path / "old.sqlite"}'
    dm = open_writable(url)
    dm.init_archive()
    with dm.engine.begin() as connection:
        connection.execute(text(f'DROP TABLE {ARCHIVE_SCHEMA_TABLE}'))  # what an archive from before 2 Nov 2026 looks like
    with pytest.raises(ArchiveSchemaMismatch, match=f'version 1;.*version {ARCHIVE_SCHEMA_VERSION}'):
        open_writable(url).init_archive()


def test_same_version_with_another_fingerprint_is_a_warning(tmp_path, caplog):
    url = f'sqlite:///{tmp_path / "fp.sqlite"}'
    dm = open_writable(url)
    dm.init_archive()
    with dm.engine.begin() as connection:
        connection.execute(text(f"UPDATE {ARCHIVE_SCHEMA_TABLE} SET fingerprint = 'abc'"))
    with caplog.at_level(logging.WARNING, logger='archivable.schema'):
        open_writable(url).init_archive()
    assert 'without a version bump' in caplog.text and 'abc' in caplog.text


def test_an_unprepared_data_manager_cannot_open_an_archive(tmp_path):
    dm = LocalDataManager(None, {'url': f'sqlite:///{tmp_path / "bare.sqlite"}'})
    with pytest.raises(RuntimeError, match='declare_schema'):
        dm.init_archive()


# ---------------------------------------------------------------- the reader
def test_open_archive_reports_the_version_and_none_without_the_table(tmp_path, caplog):
    url = f'sqlite:///{tmp_path / "read.sqlite"}'
    open_writable(url).init_archive()
    reader = open_archive(url)
    assert reader.schema_version == ARCHIVE_SCHEMA_VERSION
    assert reader.archive_schema_record()['fingerprint'] == archive_schema_fingerprint()
    with pytest.raises(RuntimeError, match='opened for reading'):
        reader.init_archive()

    with reader.engine.begin() as connection:
        connection.execute(text(f'DROP TABLE {ARCHIVE_SCHEMA_TABLE}'))
    with caplog.at_level(logging.WARNING, logger='archivable.reader'):
        old = open_archive(url)
    assert old.schema_version is None and old.archive_schema_record() is None
    assert 'archive schema version 1' in caplog.text and f'TENT {tent_version()} writes version' in caplog.text

    assert open_archive(f'sqlite:///{tmp_path / "empty.sqlite"}').schema_version is None
