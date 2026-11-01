"""
TENT's side of the archive.

The archive machinery (Archivable, the DataManager base and its Config, LocalDataManager, NullDataManager,
ArchiveReader, table and enumeration definitions, the archive schema version) is the lib-archivable library's
(``archivable``). This package holds what is TENT's own: the archive schema version of the definitions TENT ships, the
definitions and enumerations themselves, the ArchiveSchema a TENT data manager declares, and ``prepare``, which puts
them on a data manager. ``tent.data_manager.reader`` opens an existing archive with TENT's canonical queries.
"""
import importlib
import importlib.metadata
import logging
from enum import Enum

from archivable import ArchiveSchema, DataManager, archive_schema_definition, enumeration_definitions, schema_fingerprint

import tent.enumerations

_log = logging.getLogger(__name__)

# The version of the set of table definitions and enumerations TENT ships (known_archive_definitions() and
# known_enumerations()). It is incremented whenever any shipped definition or enumeration changes, so that a database
# says which TENT wrote it: the historian refuses a database or a node of another version, and tent-analysis reports
# what it is reading. Version 1 is every archive written before 2 November 2026; those databases have no archive_schema
# table. Version 2 (2 November 2026) is the first to carry the table, with a tent_version column. Version 3 (2 November
# 2026) is the library's archive_schema table (one row per application: application, version, fingerprint,
# application_version, created) and a fingerprint that covers the enumerations. The fingerprint of each version is recorded in
# tests/unit_tests/data_manager_tests/test_archive_schema.py; that test fails when a definition changes without a bump.
ARCHIVE_SCHEMA_VERSION = 3
APPLICATION = 'TENT'


def known_archive_definitions() -> list[dict]:
    """Archive definitions of the node and of every class TENT ships (tent.registry.SHIPPED), plus the archive_schema
    table, de-duplicated by table name. This is the set ARCHIVE_SCHEMA_VERSION versions and archive_schema_fingerprint()
    hashes, so a shipped class that adds or changes a table changes the fingerprint and the version must be bumped."""
    # Imported lazily: these modules import this package.
    from tent import registry
    from tent.transactive_node import TransactiveNode
    classes = [TransactiveNode]
    for class_name, location in sorted(registry.SHIPPED.items()):
        module_name, _, attribute = location.partition(':')
        cls = getattr(importlib.import_module(module_name), attribute)
        if hasattr(cls, 'get_archive_definitions'):
            classes.append(cls)
    definitions, seen = [], set()
    for cls in classes:
        for definition in cls.get_archive_definitions() or ():  # a class with nothing to archive returns None
            if definition['name'] not in seen:
                seen.add(definition['name'])
                definitions.append(definition)
    definitions.append(archive_schema_definition())
    return definitions


def known_enumerations() -> list[type[Enum]]:
    """The enumerations TENT's column definitions may name: every member of tent.enumerations, in its declared order."""
    return [getattr(tent.enumerations, name) for name in tent.enumerations.__all__]


def archive_schema_fingerprint() -> str:
    """SHA-256 of the canonical JSON of every shipped table definition and enumeration. Two TENTs with the same
    fingerprint write the same schema; a fingerprint that changes while ARCHIVE_SCHEMA_VERSION does not is a definition
    change without a version bump, which the recorded-fingerprint test catches."""
    return schema_fingerprint(known_archive_definitions(), enumeration_definitions(known_enumerations()))


def tent_version() -> str:
    """The installed TENT distribution's version, or 'unknown' when TENT runs from a source tree that is not installed."""
    try:
        return importlib.metadata.version('tent')
    except importlib.metadata.PackageNotFoundError:
        return 'unknown'


def archive_schema() -> ArchiveSchema:
    """What this TENT declares on its data managers: the application name, ARCHIVE_SCHEMA_VERSION, the fingerprint of
    the shipped definitions and the installed TENT version."""
    return ArchiveSchema(APPLICATION, ARCHIVE_SCHEMA_VERSION, archive_schema_fingerprint(), tent_version())


def prepare(data_manager: DataManager) -> DataManager:
    """Declare TENT's archive schema on `data_manager` and register TENT's enumerations, so the shipped table
    definitions can be added and the archive opened. The node does this to the data manager it builds; tests and tools
    that build one by hand do the same."""
    data_manager.declare_schema(archive_schema())
    data_manager.register_enumerations(known_enumerations())
    return data_manager
