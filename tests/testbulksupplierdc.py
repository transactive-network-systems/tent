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

# from datetime import datetime, timedelta, date, time
# from dateutil import relativedelta

# from vertex import Vertex
from tent.utils.helpers import *
# from transactive_record import TransactiveRecord
from tent.market import Market
from tent.containers.time_interval import TimeInterval
# from neighbor_model import Neighbor
# from local_asset_model import LocalAsset
# from TransactiveNode import TransactiveNode
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC
# from const import *


def test_update_vertices():
    print('Running BulkSupplier_dc.test_update_vertices()')

    print('Case 1: Expect two active vertices when neither demand charges nor losses are in play. ')
    dt = datetime.now()
    dt = dt.replace(hour=1, month=1)  # an example LLH hour
    test_neighbor = BulkSupplierDC()
    test_neighbor.maximum_power = 100
    test_neighbor.minimum_power = 0
    test_neighbor.loss_factor = 0
    test_neighbor.demand_rate = 0  # CAUTION: the method makes sure this is as in the supplier table.
    test_neighbor.demand_threshold = 101  # Placing this just above the vertices' power range
    test_market = Market()
    test_market.neighbors = [test_neighbor]
    test_time_interval = TimeInterval(market=test_market, duration=timedelta(hours=1), start_time=dt)
    test_market.time_intervals = [test_time_interval]
    # 210803DJH: The market clearing time must be defined because it is needed by the new data_manager.py for
    #            IntervalValue objects.
    test_market.market_clearing_time = dt

    try:
        test_neighbor.update_vertices(test_market)
        print('  - The method ran without errors.')
    except RuntimeWarning as message:
        print('  - ERRORS ENCOUNTERED: ' + message)

    assert len(test_neighbor.activeVertices) == 2, 'An unexpected number of active vertices were found: ' \
                                                   + str(len(test_neighbor.activeVertices))
    assert test_neighbor.activeVertices[0].value.power in [0, 100], 'First active vertex power was not as expected.'
    assert test_neighbor.activeVertices[1].value.power in [0, 100], 'Second active vertex power was not as expected.'
    assert test_neighbor.activeVertices[0].value.marginal_price == 0.0366, \
        'First active vertex marginal price is not as expected: ' \
        + str(test_neighbor.activeVertices[0].value.marginal_price)
    assert test_neighbor.activeVertices[1].value.marginal_price == 0.0366, \
        'Second active vertex marginal price is not as expected: ' \
        + str(test_neighbor.activeVertices[1].value.marginal_price)

    print('Case 2: Same as Case 1, but let demand charges be in play. ')
    dt = datetime(2021, 3, 3, 13)
    # dt = dt.replace(hour=10, month=1)  # an example HLH hour
    test_neighbor = BulkSupplierDC()
    test_neighbor.maximum_power = 100
    test_neighbor.minimum_power = 0
    test_neighbor.loss_factor = 0
    test_neighbor.demand_rate = 0  # CAUTION: the method makes sure this is as in the supplier table.
    test_neighbor.demand_threshold = 50  # Placing this in the middle of the vertices' power range
    test_market = Market()
    test_market.neighbors = [test_neighbor]
    test_time_interval = TimeInterval(market=test_market, duration=timedelta(hours=1), start_time=dt)
    test_market.time_intervals = [test_time_interval]
    # 210803DJH: data_manager.py requires that the market be assigned a market clearing time.
    test_market.market_clearing_time = dt

    try:
        test_neighbor.update_vertices(test_market)
        print('  - The method ran without errors.')
    except RuntimeWarning as message:
        print('  - ERRORS ENCOUNTERED: ' + message)

    # 200806DJH: The method now orders the resulting vertices by power and marginal price, which makes it easier to
    # automate testing.
    assert len(test_neighbor.activeVertices) == 4, 'An unexpected number of active vertices were found: ' \
                                                   + str(len(test_neighbor.activeVertices))
    assert test_neighbor.activeVertices[0].value.power == 0, 'First active vertex power was not as expected: ' \
                                                             + str(test_neighbor.activeVertices[0].value.power)
    assert test_neighbor.activeVertices[1].value.power == 50, 'Second active vertex power was not as expected: ' \
                                                              + str(test_neighbor.activeVertices[1].value.power)
    assert test_neighbor.activeVertices[2].value.power == 50, 'Third active vertex power was not as expected: '\
                                                              + str(test_neighbor.activeVertices[2].value.power)
    assert test_neighbor.activeVertices[3].value.power == 100, 'Fourth active vertex power was not as expected: '\
                                                               + str(test_neighbor.activeVertices[3].value.power)
    assert [test_neighbor.activeVertices[x].value.marginal_price for x in range(4)] == \
           [0.03641, 0.03641, 9.31641, 9.31641], 'Marginal prices were not as expected.'

    print('test_update_vertices() ran to completion.\n')


if __name__ == '__main__':
    print('Running tests in testbulksupplierdc.py\n')
    # 200804DJH: test_update_dc_threshold is within superclass Neighbor and will be tested there, not here.
    test_update_vertices()
    print('Tests in testbulksupplierdc.py ran to completion.\n')
