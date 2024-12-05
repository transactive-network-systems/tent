from local_asset_model import LocalAsset
from measurement_type import MeasurementType
from meter_point import MeterPoint


class BuildingZoneModel(LocalAsset):

    def __init__(self,
                 space_capacity=10,  # [kWht/deg. C] Thermal capacity of zone space as a function of its temp. change
                 mass_capacity=10, # [kWht/deg. C] Thermal capacity of zone mass as a function of its temp. change
                 envelope_conduction=1,  # [kWt/deg. C] Thermal conduction between zone space and ambient
                 mass_conduction=1,  # [kWt/deg. C] Thermal conduction between zone space and mass
                 heating_power=10,  # [signed kW] Maximum active electric heating power
                 cooling_power=10,  # [signed kW] Maximum active electric cooling power
                 cop_heating_0=2,  # Heating Coefficient of Performance at 0 deg. C ambient
                 cop_heating_40=5,  # Heating Coefficient of Performance at 40 deg. C ambient
                 cop_cooling_0=5,  # Cooling Coefficient of Performance  at 0 deg. C ambient
                 cop_cooling_40=2,  # Cooling Coefficient of Performance  at 40 deg. C ambient
                 max_indoor_temperature=26.7,  # [deg. C] Maximum scheduled indoor temperature
                 min_indoor_temperature=15.6  # [deg. C] Minimum scheduled indoor temperature
                 ):

        # Properties and methods inherited from LocalAsset class:
        super(LocalAsset, self).__init__(description='Thermal model of single building zone')

        # Assign new constant zone model properties for this object:
        self.spaceCapacity = abs(space_capacity)
        self.massCapacity = abs(mass_capacity)
        self.envelopeConduction = abs(envelope_conduction)
        self.massConduction = abs(mass_conduction)
        self.heatingPower = -abs(heating_power)
        self.coolingPower = -abs(cooling_power)
        self.copHeating0 = abs(cop_heating_0)
        self.copHeating40 = abs(cop_heating_40)
        self.copCooling0 = abs(cop_cooling_0)
        self.copCooling40 = abs(cop_cooling_40)

        # NOTE: These limits are initialized constant by default, but they may be configured to change in corresponding
        #       hours [0:23].
        self.maxTemperatureSchedule = [max_indoor_temperature] * 24  # [deg. C] Scheduled upper indoor temperature limit
        self.minTemperatureSchedule = [min_indoor_temperature] * 24  # [deg. C] Scheduled lower indoor temperature limit

        # These are variable properties of a building zone:
        self.currentTemperature = 21  # [deg. C] Current indoor temperature state

        self.maximumPower = 0
        self.minimumPower = min([self.coolingPower, self.heatingPower, 0.0])

        cnt = round(10 * (max_indoor_temperature - min_indoor_temperature))
        TEMPERATURE_VECTOR = [min_indoor_temperature + n / 10 for n in range(cnt+1)]
        TEMPERATURE_VECTOR = [round(n, 1) for n in TEMPERATURE_VECTOR]

        # POWER_VECTOR: Vector of 101 average electric control power levels. Here, cooling power is defined negative,
        # and heating power positive. System state is unique to a trajectory of average control power levels chosen from
        # this vector.
        cooling_vector = [-abs(self.coolingPower) + abs(self.coolingPower) * n / 50 for n in range(51)]
        heating_vector = [abs(self.heatingPower) + abs(self.heatingPower) * n / 50 for n in range(51)]
        POWER_VECTOR = sorted(set(cooling_vector + heating_vector))

    def schedule_power(self, market):
        """
           COP
        P ----->  T_z -- Z_env -- T_amb
                /  |
             C_z   Z_mass
                   |
                  Tm -- C_mass
        :param market: Market object that owns the market period(s) to be scheduled.
        :return: None. Populates local asset's list of scheduled powers.
        preliminary: market interval costs must have been predicted or discovered for all current market intervals.
        """
        self.update_current_temperature()

        end_temperature = self.currentTemperature

        intervals = market.time_intervals

        for interval in intervals:
            starting_temperature = end_temperature
            # TODO: The scheduled value(s) are determined by optimization of net comfort - cost.
            # Note that the flexibility should be estimated in update_vertices() method.

    def update_vertices(self, market):
        pass
        # NOTE determine flexibility here using perturbation of optimal scheduled powers.

    def update_current_temperature(self):
        if self.meterPoints:
            for n in range(len(self.meterPoints)):
                meter_point = self.meterPoints[n]
                if meter_point.mesurementType == MeasurementType.Temperature:
                    self.currentTemperature = meter_point.currentMeasurement
        return None

    def get_forecast_temperatures(self, market):
        """
        Use a service to predict the outdoor temperatures that correspond to a set of market time intervals.
        :return: vector of forecast temperatures [deg. C]
        """
        temperatures = None
        return temperatures

    @classmethod
    def calculate_utility_cost(cls,
                               zone_temperature=22.2,       # [deg. C] zone temperature being valued
                               set_point_temperature=22.2,  # [deg. C] Ideal zone temperature set point
                               minimum_temperature=15.6,    # [deg. C] minimum zone temperature
                               maximum_temperature=26.7,    # [deg.C] maximum zone temperature
                               cost_coefficient=1,          # Converts discomfort to cost at min. and max. temperatures
                               gamma=2):                    # determines the sharpness of the penalty function
        """
        This function calculates the utility cost of a given interior temperature. The resulting penalty cost is zero at
        an ideal zone temperature, equal to a cost coefficient at either a minimum or maximum zone temperature, and may
        be considerably more than teh cost coefficient outside the region [T_min, T_max]. The sharpness of the penalty
        at T_min and T_max is affected by exponent parameter gamma.
        :return: a penalty cost [$]
        Don Hammerstrom, PNNL
        May 2022
        """
        n = zone_temperature - set_point_temperature  # the numerator
        if zone_temperature >= set_point_temperature:
            d = maximum_temperature - set_point_temperature  # a denominator
        else:
            d = minimum_temperature - set_point_temperature  # a denominator
        return cost_coefficient * (n / d) ** gamma


if __name__ == '__main__':
    BuildingZoneModel()
