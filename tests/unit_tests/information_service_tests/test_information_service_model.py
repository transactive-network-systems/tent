"""InformationServiceModel construction and archive definitions."""
import pytest

from tent.base import Entity
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.information_service_model import InformationServiceModel
from tent.meter_point import MeterPoint
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


class ConstantService(InformationServiceModel):
    def update_information(self, market):
        return None


@pytest.fixture
def tn():
    Timer.simulation = False
    return MockTN()


def test_type_and_units_accept_names_members_and_values(tn):  # audit T-31
    by_name = ConstantService(tn, name='a', information_type='Temperature', information_units='degF')
    by_member = ConstantService(tn, name='b', information_type=MeasurementType.Temperature,
                                information_units=MeasurementUnit.degF)
    by_value = ConstantService(tn, name='c', information_type=MeasurementType.Temperature.value,
                               information_units=MeasurementUnit.degF.value)
    for service in (by_name, by_member, by_value):
        assert service.information_type is MeasurementType.Temperature
        assert service.information_units is MeasurementUnit.degF
        assert service.predicted_values.type is MeasurementType.Temperature
    with pytest.raises(KeyError):
        ConstantService(tn, name='d', information_type='NoSuchType')


def test_junction_tables_reference_the_entity_table():  # audit T-INC-18
    definitions = {d['name']: d for d in InformationServiceModel.get_archive_definitions()
                   + MeterPoint.get_archive_definitions() + Entity.get_archive_definitions()}
    for junction, link in (('entity_information_service', 'information_service_id'),
                           ('entity_meter_point', 'meter_point_id')):
        columns = {c['name']: c for c in definitions[junction]['columns']}
        assert columns['entity_id']['foreign_key'] == 'entity.id', junction
        assert columns['entity_id']['kwargs'] == {'primary_key': True}
        assert columns[link]['kwargs'] == {'primary_key': True}, 'a pair is a row; neither half alone identifies it'
