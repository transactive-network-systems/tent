import abc
import logging

from weakref import WeakSet

from tent.base.component import Component
from tent.containers import Measurement, SDCurve, SDMeasurement, TimeInterval, Vertex
from tent.enumerations import MeasurementType, MeasurementUnit

_log = logging.getLogger(__name__)


class Entity(Component, abc.ABC):
    def __init__(self, cost_parameters: list[float], default_vertices: list[Vertex | list[float] | dict],
                 description: str, information_services: list, location: str, maximum_power: float, meter_points: list,
                 minimum_power: float, name: str, transactive_node):
        super(Entity, self).__init__(description, name, transactive_node)
        # cost_parameters: [float, float, float] Coefficients of quadratic cost function
        # TODO: What are reasonable defaults for cost parameters? Is [1,1,1] better than [0,0,0]?
        self.cost_parameters: list[float] = [float(cp) for cp in cost_parameters] if cost_parameters else [0.0, 0.0, 0.0]
        if len(self.cost_parameters) != 3:
            raise ValueError(f'{name}: cost_parameters must have three coefficients, got {cost_parameters!r}')
        self.location = str(location)  # [text]
        self.maximum_power = float(maximum_power)  # [avg.kW, signed] Asset's physical "hard" constraint
        self.minimum_power = float(minimum_power)  # [avg.kW, signed] Asset's physical "hard" constraint

        # Setup Vertices for Default Supply/Demand Curve
        self.default_vertices = SDCurve(None, None,
                                        default_vertices if default_vertices
                                            else [Vertex(price=float("inf"), cost=0.0, quantity=1.0)])
        if len(self.default_vertices) == 1:
            self.default_vertices.vertices[0].price = float('inf')

        # [InformationService] See class InformationService
        self.information_services = WeakSet()
        information_services = information_services if information_services is not None else []
        if all([isinstance(ism, Component) for ism in information_services]):
            self.information_services.update(information_services)
        elif all([isinstance(ism, str) for ism in information_services]):
            self.information_services.update(self.tn.get_isms_by_attribute('name', information_services))
        else:
            raise ValueError(f'All information services assigned to LocalAsset {self.name} must be an instance of class'
                             f' InformationServiceModel or the name of an InformationServiceModel held by the'
                             f' TransactiveNode.')

        # [MeterPoint] See class MeterPoint
        self.meter_points = WeakSet()
        meter_points = meter_points if meter_points is not None else []
        if all([isinstance(mp, Component) for mp in meter_points]):
            self.meter_points.update(meter_points)
        elif all([isinstance(mp, str) for mp in meter_points]):
            self.meter_points.update(self.tn.get_meters_by_attribute('name', meter_points))
        else:
            raise ValueError(f'All meter points assigned to LocalAsset {self.name} must be an instance of class'
                             f' MeterPoint or the name of a MeterPoint held by the TransactiveNode.')

        ##################################
        # Dynamically assigned attributes:
        ##################################
        # Owned measurements are archived with this entity's row (Component.archive), not on construction.
        self.active_vertices = SDMeasurement(self, MeasurementType.ActiveVertex, archive_now=False)
        self.dual_costs = Measurement(self, MeasurementType.DualCost, MeasurementUnit.Monetary, archive_now=False)
        self.production_costs = Measurement(self, MeasurementType.ProductionCost, MeasurementUnit.Monetary, archive_now=False)
        self.reserve_margins = Measurement(self, MeasurementType.ReserveMargin, MeasurementUnit.kW, archive_now=False)  # [avg.kW, signed]
        self.scheduled_powers = Measurement(self, MeasurementType.ScheduledPower,
                                            MeasurementUnit.kW, archive_now=False)  # [avg.kW, signed]
        self.total_dual_cost = 0.0  # [$] Sum of dual costs in forward time intervals
        self.total_production_cost = 0.0  # [$, non-negative] Sum of production costs in forward intervals

    def calculate_reserve_margin(self, market):
        """ Estimate available (spinning) reserve margin for this asset.

        NOTES:
            This method works with the simplest base classes that have constant power and therefore provide no spinning
                reserve. This method may be redefined by subclasses of the local asset model to add new features or
                capabilities.
            This calculation will be more meaningful and useful after resource commitments and uncertainty estimates
                become implemented. Until then, reserve margins may be tracked, even if they are not used.
        PRESUMPTIONS:
            - Active time intervals exist and have been updated
            - The asset's maximum power is a meaningful and accurate estimate of the maximum power level that can be
                achieved on short notice, i.e., spinning reserve.
        INPUTS:
            market - Market object

        OUTPUTS:
            Modifies self.reserveMargins - an array of estimated (spinning) reserve margins in active time intervals
        """
        for time_interval in market.time_intervals:
            # Calculate the reserve margin for the indexed interval. This is the non-negative difference between the
            # maximum asset power and the scheduled power. In principle, generation may be increased or demand decreased
            # by this quantity to act as spinning reserve.

            # Find the scheduled power and SDCurve in the indexed time interval
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval)
            sd_curve = self.active_vertices.get_by_interval(time_interval)

            # Calculate the reserve margin in the indexed time interval. The reserve margin is the difference between
            #  the maximum operational power value in the interval and the scheduled power. The operational maximum
            #  should be less than the object's hard physical power constraint, so a check is in order. Start with the
            #  hard physical constraint (self.maximum_power).
            # Calculate the operational maximum constraint, which is the highest point on the supply/demand curve
            # (subject to physical limits) that represents the residual flexibility of the asset in the time interval.
            # If there are no active vertices, use the hard physical constraint (maximum power).
            # TODO: max_quantity may currently be returning a vertex. This would require using *.quantity on the following line.
            operational_constraint = sd_curve.max_quantity(self.maximum_power) if sd_curve else self.maximum_power  # [avg.kW]
            reserve_margin = max(0, operational_constraint - scheduled_power.value)  # [avg. kW]

            self.reserve_margins.update(time_interval, reserve_margin)

    def update_dual_costs(self, market):
        """Update the dual cost for all active time intervals"""
        for time_interval in market.time_intervals:
            # Find the marginal price, scheduled power, and production cost for the indexed time interval.
            marginal_price = market.marginal_prices.get_by_interval(time_interval)  # a marginal price [$/kWh]
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval)  # [avg.kW]
            production_cost = self.production_costs.get_by_interval(time_interval)  # [$]

            # Dual cost in the time interval is calculated as production cost, minus the product of marginal price,
            # scheduled power, and the duration of the time interval.
            duration = time_interval.duration.total_seconds() / 3600
            dual_cost = production_cost - (marginal_price * scheduled_power * duration)  # [$]

            self.dual_costs.update(time_interval, dual_cost)

        # Sum the total dual cost and save the value.
        # 200929DJH: This is corrected to sum only the dual costs in the current market
        # TODO: If there are two markets operating simultaneously, won't the second assignment clobber the first?
        self.total_dual_cost = self.dual_costs.total(market)

        _log.debug(f"{self.name} dual costs are: {self.dual_costs.log_report}")

    def _get_production_costs(self, time_interval):
        """ Get production costs from vertices or a formula, if none are available.
            NOTE: Cost might need to be handled differently and redefined in subclasses.
        """
        scheduled_power = self.scheduled_powers.get_by_interval(time_interval)  # [avg.kW]
        sd_curve = self.active_vertices.get_by_interval(time_interval)
        if sd_curve:
            return sd_curve.prod_cost_from_vertices(scheduled_power.value)  # [$]
        else:
            # TODO: This function was unused, but we weren't handling case with no sd_curve.
            #  This seems appropriate to use it here as a backup. Is it correct?
            return self.prod_cost_from_formula(time_interval)  # [$]

    def update_production_costs(self, market):
        """Calculate the costs of generated energies.
        """
        for time_interval in market.time_intervals:
            production_cost = self._get_production_costs(time_interval)
            self.production_costs.update(time_interval, production_cost)

            # Sum the total production cost.
            # 200929DJH: In Version 3, this sum must be made only within the current market object.
            # TODO: If there are two markets operating simultaneously, won't the second assignment clobber the first?
        self.total_production_cost = self.production_costs.total(market)  # [$]

        _log.debug(f"{self.name} asset model production costs are: {self.production_costs.log_report}")

    def production(self, price: float, time_interval: TimeInterval) -> float:
        """ Find economic power production [avg.kW] in this time interval corresponding to the given price.
                This is performed as a linear interpolation of a discrete set of price-ordered vertices (SDCurve).
                Values are positive for generation.
        """
        sd_curve = self.active_vertices.get_by_interval(time_interval)
        if not sd_curve:  # No active vertices were found in the given time interval
            raise Exception(f'No active vertices were found for {self.name}'
                            f' in interval: {time_interval.name}.')
        else:
            return sd_curve.power_from_vertices(price)


    def prod_cost_from_formula(self, time_interval):
        """Calculate production cost from a quadratic production-cost formula

        This formulation allows for a quadratic cost function. Objects have cost parameters that allow the calculation
         of production cost from the power and these cost coefficients production cost = a0 + a1*p + 0.5*a2*p^2

        INPUTS:
            entity - Either a NeighborModel or LocalAssetModel object
            time_interval - time interval (See TimeInterval class)

        OUTPUTS:
            cost - production cost in absolute dollars for time interval ti [$]
        """
        # Get the object's quadratic cost coefficients and scheduled power
        a = self.cost_parameters
        sp = self.scheduled_powers.get_by_interval(time_interval)  # [avg.kW]

        # Calculate the production cost from the quadratic cost formula: [$]=[$/h]+[$/h]+[$/h]*[h]
        return (a[0] + (a[1] * sp) + (0.5 * a[2] * sp ** 2)) * (time_interval.duration.total_seconds() / 3600)

    @abc.abstractmethod
    def get_record(self) -> dict:
        record = super(Entity, self).get_record()
        record.update({
            'cost_parameters_0': self.cost_parameters[0],
            'cost_parameters_1': self.cost_parameters[1],
            'cost_parameters_2': self.cost_parameters[2],
            # 'default_vertices': self.default_vertices,
            'location': self.location,
            'maximum_power': self.maximum_power,
            'minimum_power': self.minimum_power
        })
        return record

    def archive(self, records: list[dict] = None):
        records = records if records else []
        records.extend([
            {'table_name': 'entity_meter_point', 'data': {'entity_id': self.id, 'meter_point_id': m.id}}
            for m in self.meter_points
        ])
        records.extend([
            {'table_name': 'entity_information_service', 'data': {'entity_id': self.id, 'information_service_id': i.id}}
            for i in self.information_services
        ])
        super(Entity, self).archive(records)

    @classmethod
    @abc.abstractmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Entity, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "entity",
                "inherits": "component",
                "columns": [
                    {"name": "id", "type": "Uuid", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "cost_parameters_0", "type": "Float"},
                    {"name": "cost_parameters_1", "type": "Float"},
                    {"name": "cost_parameters_2", "type": "Float"},
                    # {"name": "default_vertices", "foreign_key": "sd_curve.id"},
                    {"name": "location", "type": "String", "type_args": [255]},
                    {"name": "maximum_power", "type": "Float"},
                    {"name": "minimum_power", "type": "Float"}
                ]
            }
        ])
        return definitions