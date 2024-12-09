"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
This material was prepared as an account of work sponsored by an agency of the
United States Government. Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in th.e development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or
any information, apparatus, product, software, or process disclosed, or
represents that its use would not infringe privately owned rights.
Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the
United States Government or any agency thereof, or Battelle Memorial Institute.
The views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""
import logging

from datetime import datetime

from tent.containers import Vertex
from tent.enumerations import MeasurementType
from tent.utils.log import setup_logging

setup_logging()
_log = logging.getLogger(__name__)

VERBOSE = False
METHOD = 1  # Comma-Separated Value method
# METHOD = 2  # Database methods
MAX_ROWS = 1000  # Maximum number of table rows allowed. Old rows are removed. Use float('Inf') for unlimited rows.
CSV_DIRECTORY = './CSV_DATA'  # Static csv data directory relative to working directory.
DATE_FORMAT = "%Y-%m-%d"  # Simple date format like '2021-01-26'.
TIME_FORMAT = "%H:%M:%S"  # Simple time format like '13:14:15'.


def append_information_service_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_information_service_table().")


def append_interval_value_table(method=METHOD, objects=None):
    """
    Receive a list of IntervalValue objects and make a row for each in a data table.
    :param method: {1: CSV, 2: DATABASE} Only CSV is implemented.
    :param objects: List of IntervalValue objects
    :return:
    """
    import csv
    import os
    if VERBOSE:
        pass #_log.debug("Made it to append_interval_value_table().")
    if not isinstance(objects, list):
        objects = [objects]
    header = ['TIMESTAMP', 'SOURCE_TYPE', 'SOURCE_NAME', 'MARKET_SERIES', 'MARKET_CLEARING_DATE',
              'MARKET_CLEARING_TIME', 'INTERVAL_STARTING_DATE', 'INTERVAL_STARTING_TIME', 'MEASUREMENT_TYPE',
              'VALUE_1', 'VALUE_2', 'VALUE_3']
    data_folder = "./CSV_DATA"
    datafile = "./CSV_DATA/IntervalValues.csv"
    TIMESTAMP = format(datetime.now())

    if not os.path.isfile(datafile):
        # The data file does not exist.
        if VERBOSE:
            pass #_log.debug("File './CSV_DATA/IntervalValues.csv' does not exist.")
        if not os.path.isdir(data_folder):
            # The data directory does not exist either.
            if VERBOSE:
                pass #_log.debug("Directory './CSV_DATA' does not exist. Create it.")
            os.mkdir(data_folder)
        with open(datafile, 'w', newline='') as write_obj:  # Open the new file and write its header row.
            if VERBOSE:
                pass #_log.debug("Creating the file './CSV_DATA/IntervalValues.csv' with header.")
            csv_writer = csv.writer(write_obj, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(header)
            write_obj.close()

    file = open(datafile, 'a', newline='')
    if VERBOSE:
        pass #_log.debug("Opening './CSV_DATA/IntervalValues.csv' to append row(s).")
    appender = csv.writer(file)

    for obj in objects:
        SOURCE_TYPE = obj.associatedClass
        SOURCE_NAME = obj.associatedObject  # 211116DJH: TODO: This should probably be the object's name.
        MARKET_SERIES = obj.market.market_series_name
        # 211109DJH: The use of strftime() throws errors if these datetime objects were not defined. This approach
        #            should be more tolerant.
        if obj.market.market_clearing_time:
            MARKET_CLEARING_DATE = obj.market.market_clearing_time.strftime(DATE_FORMAT)
            MARKET_CLEARING_TIME = obj.market.market_clearing_time.strftime(TIME_FORMAT)
        else:
            MARKET_CLEARING_DATE = ''
            MARKET_CLEARING_TIME = ''
        if obj.time_interval.start_time:
            INTERVAL_STARTING_DATE = obj.time_interval.start_time.strftime(DATE_FORMAT)
            INTERVAL_STARTING_TIME = obj.time_interval.start_time.strftime(TIME_FORMAT)
        else:
            INTERVAL_STARTING_DATE = ''
            INTERVAL_STARTING_TIME = ''
        MEASUREMENT_TYPE = MeasurementType.get(obj.measurement_type.value)
        value_type = type(obj.value)
        if value_type != Vertex:
            VALUE_1 = obj.value
            VALUE_2 = ''
            VALUE_3 = ''
        else:
            VALUE_1 = obj.value.marginal_price
            VALUE_2 = obj.value.power
            VALUE_3 = obj.value.record
        record = [TIMESTAMP, SOURCE_TYPE, SOURCE_NAME, MARKET_SERIES, MARKET_CLEARING_DATE, MARKET_CLEARING_TIME,
                  INTERVAL_STARTING_DATE, INTERVAL_STARTING_TIME, MEASUREMENT_TYPE, VALUE_1, VALUE_2, VALUE_3]

        appender.writerow(record)
        if VERBOSE:
            pass #_log.debug('Appending record.')

    file.close()
    if VERBOSE:
        pass #_log.debug('Closing file.')
    return None


def append_local_asset_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_local_asset_table().")


def append_market_object_table(method=METHOD, objects=None):
    """
    Receive a list of Market objects and make a row for each in a data table.
    :param method: {1: CSV, 2: DATABASE} Only CSV is curently implemented as of January 2021.
    :param objects: List of Market objects
    :return:
    """
    import csv
    import os
    VERBOSE = True
    if VERBOSE:
        pass #_log.debug("Made it to append_market_object_table().")
    if not isinstance(objects, list):
        objects = [objects]
    header = ['TIMESTAMP', 'MARKET_NAME', 'MARKET_SERIES', 'NUMBER_INTERVALS', 'MARKET_CLEARING_DATE',
              'MARKET_CLEARING_TIME', 'DELIVERY_START_DATE', 'DELIVERY_START_TIME', 'TOTAL_DELIVERY_HOURS',
              'TOTAL_GENERATION', 'TOTAL_DEMAND', 'TOTAL_PRODUCTION_COST', 'TOTAL_DUAL_COST']
    data_folder = "./CSV_DATA"
    datafile = "./CSV_DATA/MarketObjects.csv"
    dt = datetime.now()
    TIMESTAMP = format(dt, DATE_FORMAT) + ' ' + format(dt, TIME_FORMAT)

    if not os.path.isfile(datafile):
        # The data file does not exist.
        if VERBOSE:
            pass #_log.debug('File ' + datafile + ' does not exist.')
        if not os.path.isdir(data_folder):
            # The data directory does not exist either.
            if VERBOSE:
                pass #_log.debug('Directory ' + data_folder + ' does not exist. Creating it.')
            os.mkdir(data_folder)
        with open(datafile, 'w', newline='') as write_obj:  # Open the new file and write its header row.
            if VERBOSE:
                pass #_log.debug('Creating the file ' + datafile + ' with header.')
            csv_writer = csv.writer(write_obj, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(header)
            write_obj.close()

    file = open(datafile, 'a', newline='')
    if VERBOSE:
        pass #_log.debug('Opening ' + datafile + ' to append row(s).')
    appender = csv.writer(file)

    for obj in objects:
        MARKET_NAME = obj.name
        MARKET_SERIES = obj.market_series_name
        NUMBER_INTERVALS = obj.intervals_to_clear
        MARKET_CLEARING_DATE = format(obj.market_clearing_time, DATE_FORMAT)
        MARKET_CLEARING_TIME = format(obj.market_clearing_time, TIME_FORMAT)
        start_time = min(obj.time_intervals.start_times)
        DELIVERY_START_DATE = format(start_time, DATE_FORMAT)
        DELIVERY_START_TIME = format(start_time, TIME_FORMAT)
        TOTAL_DELIVERY_HOURS = obj.intervals_to_clear * obj.interval_duration
        TOTAL_GENERATION = None
        TOTAL_DEMAND = None
        TOTAL_PRODUCTION_COST = None
        TOTAL_DUAL_COST = None

        record = [TIMESTAMP, MARKET_NAME, MARKET_SERIES, NUMBER_INTERVALS, MARKET_CLEARING_DATE,
                  MARKET_CLEARING_TIME, DELIVERY_START_DATE, DELIVERY_START_TIME, TOTAL_DELIVERY_HOURS,
                  TOTAL_GENERATION, TOTAL_DEMAND, TOTAL_PRODUCTION_COST, TOTAL_DUAL_COST]

        appender.writerow(record)
        if VERBOSE:
            pass #_log.debug('Appending record.')

    file.close()
    if VERBOSE:
        pass #_log.debug('Closing file.')
    return None


def append_meter_point_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_meter_point_table().")


def append_neighbor_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_neighbor_table().")


def append_time_interval_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_time_interval_table().")


def append_transactive_record_table(method=METHOD, objects=None):
    """
    Receive a list of TransactiveRecord objects and make a row for each in a data table.
    :param method: {1: CSV, 2: DATABASE} Only CSV is implemented.
    :param objects: List of TransactiveRecord objects
    :return:
    """
    import csv
    import os
    VERBOSE = True
    if VERBOSE:
        pass #_log.debug("Made it to append_transactive_record_table().")
    if not isinstance(objects, list):
        objects = [objects]
    header = ['TIMESTAMP', 'NEIGHBOR_NAME', 'DIRECTION', 'MARKET_SERIES', 'MARKET_CLEARING_DATE',
              'MARKET_CLEARING_TIME', 'INTERVAL_STARTING_DATE', 'INTERVAL_STARTING_TIME', 'RECORD',
              'PRICE', 'POWER']
    data_folder = "./CSV_DATA"
    datafile = "./CSV_DATA/TransactiveRecords.csv"
    dt = datetime.now()
    TIMESTAMP = format(dt, DATE_FORMAT) + ' ' + format(dt, TIME_FORMAT)

    if not os.path.isfile(datafile):
        # The data file does not exist.
        if VERBOSE:
            pass #_log.debug('File ' + datafile + ' does not exist.')
        if not os.path.isdir(data_folder):
            # The data directory does not exist either.
            if VERBOSE:
                pass #_log.debug('Directory ' + data_folder + ' does not exist. Creating it.')
            os.mkdir(data_folder)
        with open(datafile, 'w', newline='') as write_obj:  # Open the new file and write its header row.
            if VERBOSE:
                pass #_log.debug('Creating the file ' + datafile + ' with header.')
            csv_writer = csv.writer(write_obj, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(header)
            write_obj.close()

    file = open(datafile, 'a', newline='')
    if VERBOSE:
        pass #_log.debug('Opening ' + datafile + ' to append row(s).')
    appender = csv.writer(file)

    for obj in objects:
        NEIGHBOR_NAME = obj.neighbor_name
        DIRECTION = obj.direction
        parts1 = obj.time_interval.split(':')  # The record time interval is prepended with the market series name
        MARKET_SERIES = parts1[0]
        INTERVAL_STARTING_DATE = parts1[1][:4] + '-' + parts1[1][4:6] + '-' + parts1[1][6:8]
        INTERVAL_STARTING_TIME = parts1[1][9:11] + ':' + parts1[1][11:13] + ':' + parts1[1][13:15]
        parts2 = obj.market_name.split(':')
        MARKET_CLEARING_DATE = parts2[0][-13:-3]
        MARKET_CLEARING_TIME = parts2[0][-2:] + ':' + parts2[1] + ':' + parts2[2]
        RECORD = obj.record
        PRICE = obj.marginal_price
        POWER = obj.power

        record = [TIMESTAMP, NEIGHBOR_NAME, DIRECTION, MARKET_SERIES, MARKET_CLEARING_DATE,
                  MARKET_CLEARING_TIME, INTERVAL_STARTING_DATE, INTERVAL_STARTING_TIME, RECORD,
                  PRICE, POWER]

        appender.writerow(record)
        if VERBOSE:
            pass #_log.debug('Appending record.')

    file.close()
    if VERBOSE:
        pass #_log.debug('Closing file.')
    return None


def append_vertex_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_vertex_table().")


def append_table(method=METHOD, obj=None):
    if VERBOSE:
        pass #_log.debug("Made it to append_table method.")
    if obj is None:
        RuntimeWarning("No object was passed.")
        return
    tables = {
        'InformationServiceModel': append_information_service_table,
        'IntervalValue': append_interval_value_table,
        'LocalAsset': append_local_asset_table,
        'Market': append_market_object_table,
        'MeterPoint': append_meter_point_table,
        'Neighbor': append_neighbor_table,
        'TimeInterval': append_time_interval_table,
        'TransactiveRecord': append_transactive_record_table,
        'Vertex': append_vertex_table
        }
    if isinstance(obj, list):
        object_class = obj[0].__class__
        if any([type(x) != object_class for x in obj]):
            RuntimeWarning("An object list must contain objects of the same class.")
            return
    else:
        object_class = obj.__class__

    object_class_name = object_class.__name__
    selection = [x for x in tables if object_class_name == x]

    if len(selection) == 0:  # no table selection was identified
        object_bases = object_class.__bases__  # list of class parents
        object_classes = [object_bases[x] for x in range(len(object_bases))]  # list of parent classes
        selection = [x.__name__ for x in object_classes if x.__name__ in tables]  # table selection
        if len(selection) == 0:
            object_bases = []
            for object_class in object_classes:
                object_bases.extend(object_class.__bases__)
            object_classes = [object_bases[x] for x in range(len(object_bases))]
            selection = [x.__name__ for x in object_classes if x.__name__ in tables]
            if len(selection) == 0:
                object_bases = []
                for object_class in object_classes:
                    object_bases.extend(object_class.__bases__)
                selection = [x.__name__ for x in object_bases if x.__name__ in tables]
                if len(selection) == 0:
                    raise RuntimeWarning('No base class was identified.')
                return
            else:
                func = tables[selection[0]]
                return func(method, obj)
        else:
            func = tables[selection[0]]
            return func(method, obj)
    else:
        func = tables[selection[0]]
        return func(method, obj)


def lookup_neighbor_key(method=METHOD, obj=None):
    pass


def lookup_vertex_key(method=METHOD, obj=None):
    pass


def lookup_primary_key(method=METHOD, obj=None):
    pass


def plot_vertices(source_type=0,
                  source_name=0,
                  market_series=0,
                  interval_date=0,
                  interval_time=0,
                  measurement_type=0,
                  get_key=False,
                  plot=False,
                  verbose=False,
                  datafile="//pnl/projects/cetc/TransactiveTesting/TENT/Simulation/test_tnt/onebuilding_test_12_08/"
                           "CSV_DATA/campus_city/campus/IntervalValues.csv"
                  ):
    """
    :param datafile: string - relative or absolute directory and filename
    :param source_type: integer - dictionary index
    :param source_name: integer - dictionary index
    :param market_series: integer - dictionary index
    :param interval_date: integer - dictionary index
    :param interval_time: integer - dictionary index
    :param measurement_type: integer - dictionary index
    :param verbose: boolean - set True to get verbose verifications
    :param get_key: boolean - set True to receive data dictionary key
    :param plot: boolean -set True to plot vertex set
    :return: None
    """
    import csv
    import os
    import matplotlib.pyplot as plt

    if verbose:
        print("Made it to method plot_vertices().")

    if not os.path.isfile(datafile):  # The data file does not exist.
        print('File ' + datafile + ' does not exist.')
        raise FileNotFoundError
    else:
        if verbose:
            print('File ' + datafile + ' exists.')

    # Read the datafile:
    lines = list(csv.DictReader(open(datafile, 'r')))

    # Create dictionaries for the columns of the datafile:
    source_type_set = set([lines[i]['SOURCE_TYPE'] for i in range(len(lines))])
    source_type_dict = {i: list(sorted(source_type_set))[i] for i in range(len(source_type_set))}

    source_name_set = set([lines[i]['SOURCE_NAME'] for i in range(len(lines))])
    source_name_dict = {i: list(sorted(source_name_set))[i] for i in range(len(source_name_set))}

    market_series_set = set([lines[i]['MARKET_SERIES'] for i in range(len(lines))])
    market_series_dict = {i: list(sorted(market_series_set))[i] for i in range(len(market_series_set))}

    interval_date_set = set([lines[i]['INTERVAL_STARTING_DATE'] for i in range(len(lines))])
    interval_date_dict = {i: list(sorted(interval_date_set))[i] for i in range(len(interval_date_set))}

    interval_time_set = set([lines[i]['INTERVAL_STARTING_TIME'] for i in range(len(lines))])
    interval_time_dict = {i: list(sorted(interval_time_set))[i] for i in range(len(interval_time_set))}

    measurement_type_set = set([lines[i]['MEASUREMENT_TYPE'] for i in range(len(lines))])
    measurement_type_dict = {i: list(sorted(measurement_type_set))[i] for i in range(len(measurement_type_set))}

    if get_key:  # print a key to the CSV dictionaries:
        print('\nCSV file dictionary keys: \n'
              + '  Source types: ' + str(source_type_dict) + '\n'
              + '  Source names: ' + str(source_name_dict) + '\n'
              + '  Market series: ' + str(market_series_dict) + '\n'
              + '  Interval date range: {0: ' + str(interval_date_dict[0]) + '} to {' + str(len(interval_date_dict)-1)
                                + ': ' + str(interval_date_dict[len(interval_date_dict)-1]) + '}\n'
              + '  Interval time range: {0: ' + str(interval_time_dict[0]) + '} to {' + str(len(interval_time_dict)-1)
                                + ': ' + str(interval_time_dict[len(interval_time_dict)-1]) + '}\n'
              + '  Measurement types: ' + str(measurement_type_dict) + '\n'
              )

    items0 = []
    try:
        items0 = [source_type_dict[source_type] == lines[i]['SOURCE_TYPE']
                  for i in range(len(lines))]
    except KeyError:
        print('The source type key must be less than or equal to ' + str(len(source_type_dict)-1) + '.')
        return
    finally:
        if verbose or get_key:
            print('Source type ' + str(source_type) + ': \"' + str(source_type_dict[source_type]) + '\" ('
                  + str(sum(items0)) + ' records)')

    items1 = []
    try:
        items1 = [source_name_dict[source_name] == lines[i]['SOURCE_NAME']
                  for i in range(len(lines))]
    except KeyError:
        print('The source name key must be less than or equal to ' + str(len(source_name_dict) - 1) + '.')
        return
    finally:
        if verbose or get_key:
            print('Source name ' + str(source_name) + ': \"' + str(source_name_dict[source_name])
                  + '\" (' + str(sum(items1)) + ' records)')

    items2 = []
    try:
        items2 = [market_series_dict[market_series] == lines[i]['MARKET_SERIES']
                  for i in range(len(lines))]
    except KeyError:
        print('The market series key must be less than or equal to ' + str(len(market_series_dict) - 1) + '.')
    finally:
        if verbose or get_key:
            print('Market series ' + str(market_series) + ': \"' + str(market_series_dict[market_series])
                  + '\" (' + str(sum(items2)) + ' records)')

    items3 = []
    try:
        items3 = [interval_date_dict[interval_date] == lines[i]['INTERVAL_STARTING_DATE']
                  for i in range(len(lines))]
    except KeyError:
        print('The interval date key must be less than or equal to ' + str(len(interval_date_dict) - 1) + '.')
    finally:
        if verbose or get_key:
            print('Interval date ' + str(interval_date) + ': \"' + str(interval_date_dict[interval_date])
                  + '\" (' + str(sum(items3)) + ' records)')

    items4 = []
    try:
        items4 = [interval_time_dict[interval_time] == lines[i]['INTERVAL_STARTING_TIME']
                  for i in range(len(lines))]
    except KeyError:
        print('The interval time key must be less than or equal to ' + str(len(interval_time_dict) - 1) + '.')
    finally:
        if verbose or get_key:
            print('Interval time ' + str(interval_time) + ': \"' + str(interval_time_dict[interval_time])
                  + '\" (' + str(sum(items4)) + ' records)')

    items5 = []
    try:
        items5 = [measurement_type_dict[measurement_type] == lines[i]['MEASUREMENT_TYPE']
                  for i in range(len(lines))]
    except KeyError:
        print('The measurement type key must be less than or equal to ' + str(len(measurement_type_dict) - 1) + '.')
    finally:
        if verbose or get_key:
            print('Measurement type ' + str(measurement_type) + ': \"' + str(measurement_type_dict[measurement_type])
                  + '\" (' + str(sum(items5)) + ' records)')

    items = [items0[i] * items1[i] * items2[i] * items3[i] * items4[i] * items5[i] for i in range(len(lines))]

    if verbose:
        print('There are ' + str(sum(items)) + ' records matching all chosen keys.')

    if sum(items) == 0:
        Warning('The chosen keys resulted in no records. Stopping.')
        return None

    if plot:
        item_numbers = [i for i in range(len(lines)) if items[i] == 1]

        x = []
        y = []
        for item in item_numbers:
            x.append(lines[item]['VALUE_1'])
            y.append(lines[item]['VALUE_2'])

        if len(item_numbers)==1:
            x = [-0.1, 0, 0.1]
            y = [y[0], y[0], y[0]]

        cond = [x[i] != float('inf') and x[i] is not None
                and y[i] != float('inf') and y[i] is not None
                for i in range(len(x))]
        xf = [float(x[i]) for i in range(len(x)) if cond[i]]
        yf = [float(y[i]) for i in range(len(x)) if cond[i]]

        try:
            # Plot the vertices that have been found:
            plt.close()
            xmin = min(xf)
            xmax = max(xf)
            xdif = 0.1 * (xmax - xmin)
            plt.xlim(xmin - xdif, xmax + xdif)
            if len(item_numbers)==1:
                plt.plot(xf, yf)
            else:
                plt.plot(xf, yf, marker='*')
            plt.ylabel('quantity [avg. kW]')
            plt.xlabel('marginal price [$/kWh]')

            xpos = round(sum(xf)/len(xf), 3)
            ypos = round(sum(yf)/len(yf), 3)

            item = item_numbers[0]
            text = 'Source: ' + lines[item]['SOURCE_NAME'] + '\n' + \
                   'Series: ' + lines[item]['MARKET_SERIES'] + '\n' + \
                   '  ' + 'Date: ' + lines[item]['INTERVAL_STARTING_DATE'] + '\n' + \
                   '  ' + 'Time: ' + lines[item]['INTERVAL_STARTING_TIME'] + '\n' + \
                   '  ' + 'Type: ' + lines[item]['MEASUREMENT_TYPE']
            plt.text(xpos, ypos, text, multialignment="left")

            # Add arrow toward -infinity.
            plt.annotate("", xy=(xf[0] - xdif, yf[0]),
                         xytext=(xf[0], yf[0]),
                         arrowprops=dict(arrowstyle="->", color='b'))

            # Add arrow toward +infinity.
            plt.annotate("", xy=(xf[-1] + xdif, yf[-1]),
                         xytext=(xf[-1], yf[-1]),
                         arrowprops=dict(arrowstyle="->", color='b'))

            plt.show()

        except ZeroDivisionError:
            print('This set has no valid members.')

    return None
