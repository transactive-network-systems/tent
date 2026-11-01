import abc
import logging

from weakref import WeakSet

from tent.base.component import Component
from tent.config import ConfigModel, CostParameters, DefaultVertices, NamedConfig, NameReferences, Setting
from tent.containers import Measurement, SDCurve, SDMeasurement, TimeInterval, Vertex
from tent.enumerations import MeasurementType, MeasurementUnit

_log = logging.getLogger(__name__)


class Entity(Component, abc.ABC):
    class Config(NamedConfig):
        cost_parameters: CostParameters = Setting(
            None, reconfigurable=True,
            description='Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 '
                        '[$/h with p in kW]; null means no cost.')
        default_vertices: DefaultVertices = Setting(
            None, reconfigurable=True,
            description='Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, '
                        'continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with '
                        'those keys; null means one inelastic vertex at 1 kW.')
        information_services: NameReferences('information_service_models') = Setting(
            None, reconfigurable=True,
            description='Names of the information services (forecasts) this entity uses; null means none.')
        location: str = Setting('', reconfigurable=True, description='Free-text location.')
        maximum_power: float = Setting(0.0, reconfigurable=True,
                                       description='[avg.kW, signed, generation positive] physical upper power limit.')
        meter_points: NameReferences('meter_points') = Setting(
            None, reconfigurable=True, description='Names of the meter points that measure this entity; null means none.')
        minimum_power: float = Setting(0.0, reconfigurable=True,
                                       description='[avg.kW, signed, generation positive] physical lower power limit.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(Entity, self).__init__(cfg.description, cfg.name, transactive_node, config=cfg)
        self.cost_parameters: list[float] = list(cfg.cost_parameters)  # [a0, a1, a2] of the quadratic cost function
        self.location = str(cfg.location)  # [text]
        self.maximum_power = float(cfg.maximum_power)  # [avg.kW, signed] Asset's physical "hard" constraint
        self.minimum_power = float(cfg.minimum_power)  # [avg.kW, signed] Asset's physical "hard" constraint

        # Setup Vertices for Default Supply/Demand Curve
        self.default_vertices = self._default_curve(cfg.default_vertices)

        # [InformationService] See class InformationService
        self.information_services = WeakSet(self._resolve_references(cfg.information_services, 'information service',
                                                                     'get_isms_by_attribute'))
        # [MeterPoint] See class MeterPoint
        self.meter_points = WeakSet(self._resolve_references(cfg.meter_points, 'meter point',
                                                             'get_meters_by_attribute'))

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

    @staticmethod
    def _default_curve(vertices: list | None) -> SDCurve:
        """The default supply/demand curve from the configured vertices (a single inelastic vertex when none)."""
        curve = SDCurve(None, None, vertices if vertices else [Vertex(price=float("inf"), cost=0.0, quantity=1.0)])
        if len(curve) == 1:
            curve.replace_vertex(curve.vertices[0], price=float('inf'))
        return curve

    def _resolve_references(self, references: list | None, what: str, lookup: str) -> list:
        """The components a configured list of names refers to (components given as objects are taken as they are),
        found through the node method named `lookup`. A name that matches nothing on the node is a configuration error,
        not a silently shorter list."""
        if not references:
            return []
        if all(isinstance(reference, Component) for reference in references):
            return list(references)
        found = getattr(self.tn, lookup)('name', list(references))
        missing = sorted(set(references) - {component.name for component in found})
        if missing:
            raise ValueError(f'{self.describe(name=self.name)}: no {what} named {missing} is configured on the node; '
                             f'{what}s are referred to by the name they are configured with.')
        return found

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        derived = {'cost_parameters', 'default_vertices', 'information_services', 'meter_points'}
        super()._apply_reconfiguration(cfg, changed - derived)
        if 'cost_parameters' in changed:
            self.cost_parameters = list(cfg.cost_parameters)
        if 'default_vertices' in changed:
            self.default_vertices = self._default_curve(cfg.default_vertices)
        if 'information_services' in changed:
            self.information_services = WeakSet(self._resolve_references(cfg.information_services, 'information service',
                                                                         'get_isms_by_attribute'))
        if 'meter_points' in changed:
            self.meter_points = WeakSet(self._resolve_references(cfg.meter_points, 'meter point',
                                                                 'get_meters_by_attribute'))

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

            # An asset that has not scheduled this interval has no meaningful
            # margin to report. Skip rather than raising AttributeError on None.
            if scheduled_power is None:
                _log.warning(f'{self.name} has no scheduled power in {time_interval.name}; '
                             f'skipping reserve margin for this interval.')
                continue

            # Calculate the reserve margin in the indexed time interval. The reserve margin is the difference between
            #  the maximum operational power value in the interval and the scheduled power. The operational maximum
            #  should be less than the object's hard physical power constraint, so a check is in order. Start with the
            #  hard physical constraint (self.maximum_power).
            # Calculate the operational maximum constraint, which is the highest point on the supply/demand curve
            # (subject to physical limits) that represents the residual flexibility of the asset in the time interval.
            # If there are no active vertices, use the hard physical constraint (maximum power).
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