"""LocalAsset: reserve margins, engagement costs and schedules, scheduled powers, dual and production costs, vertices."""
import pytest
from datetime import datetime, timedelta, timezone

from tent.containers.vertex import Vertex
from tent.containers.sd_curve import SDCurve
from tent.local_asset import LocalAsset
from tests.mock_tn import MockTN
from tests.stubs import make_market



def test_calculate_reserve_margin():
    """Test calculation of reserve margins for a LocalAsset.
    
    Reserve margins are introduced but not fully integrated into code in early template versions.
    
    CASES:
    1. Uses hard maximum if no active vertices exist
    2. Vertices exist:
       2.1 Uses maximum vertex power if it is less than hard power constraint
       2.2 Uses hard constraint if it is less than maximum vertex power
       2.3 Upper flex power is greater than scheduled power assigns correct positive reserve margin
       2.4 Upper flex power less than scheduled power assigns zero value to reserve margin
    """

    # Create test node
    test_node = MockTN()

    # Create Market with modern API
    test_mkt = make_market(test_node, datetime.now(timezone.utc))

    # Get the time interval created by Market
    ti = test_mkt.time_intervals[0]

    # Create test LocalAsset with modern API
    test_model = LocalAsset(transactive_node=test_node)
    test_node.local_assets = [test_model]
    
    # Set scheduled power using modern IntervalSeries API
    test_model.scheduled_powers.update(ti, 0.0)
    test_model.maximum_power = 100

    # Run the first test case
    test_model.calculate_reserve_margin(test_mkt)
    # Access reserve_margins using modern IntervalSeries API
    reserve_margin_value = test_model.reserve_margins.get_by_interval(ti)
    assert reserve_margin_value is not None, 'A reserve margin should have been stored'
    assert reserve_margin_value == test_model.maximum_power, \
        'The method did not use the available maximum power'

    # Create some vertices and store them using SDCurve
    vertices = [
        Vertex(0, 0, -10),
        Vertex(0, 0, 10)
    ]
    sd_curve = SDCurve(measurement=None, interval=ti, vertices=vertices)
    test_model.active_vertices.update(ti, sd_curve)

    test_model.maximum_power = 100
    test_model.calculate_reserve_margin(test_mkt)
    reserve_margin_value = test_model.reserve_margins.get_by_interval(ti)
    assert reserve_margin_value == 10, 'The method should have used vertex for comparison'

    test_model.maximum_power = 5
    test_model.calculate_reserve_margin(test_mkt)
    reserve_margin_value = test_model.reserve_margins.get_by_interval(ti)
    assert reserve_margin_value == 5, 'The method should have used maximum power for comparison'

    test_model.scheduled_powers.update(ti, 20)
    test_model.maximum_power = 500
    test_model.calculate_reserve_margin(test_mkt)
    reserve_margin_value = test_model.reserve_margins.get_by_interval(ti)
    assert reserve_margin_value == 0, 'The method should have assigned zero for a neg. result'

    # Success


def test_engagement_cost():
    """Test the engagement cost method for a LocalAsset.
    
    Tests engagement cost calculation based on transition states:
    - transition = -1: disengagement
    - transition = 0: hold (no change)
    - transition = 1: engagement
    - Other values: invalid/disallowed
    
    CASES:
    1. Normal transition input arguments [-1, 0, 1]
       a. Hold transition (0) → cost = engagement_costs[1]
       b. Disengagement transition (-1) → cost = engagement_costs[0]
       c. Engagement transition (1) → cost = engagement_costs[2]
    2. Unexpected, disallowed input argument (7) → cost = None
    """

    # Create test node
    test_node = MockTN()

    # Create a test LocalAsset object with modern API
    test_model = LocalAsset(transactive_node=test_node)

    # Assign engagement costs for [disengagement, hold, engagement]
    test_model.engagement_costs = [1, 2, 3]

    # TEST 1

    transition = 0  # hold transition, unchanged

    try:
        cost = test_model.engagement_cost(transition)
    except Exception as cause:
        cost = None

    assert cost == 2, '  The method miscalculated the cost of a hold'

    transition = -1  # disengagement transition

    cost = test_model.engagement_cost(transition)
    assert cost == 1, 'The method miscalculated the cost of a disengagement'

    transition = 1  # engagement transition

    cost = test_model.engagement_cost(transition)
    assert cost == 3, 'The method miscalculated the cost of an engagement'

    # TEST 2

    transition = 7  # a disallowed transition

    try:
        cost = test_model.engagement_cost(transition)
    except Exception as cause:
        cost = None

    assert cost is None, 'The method should not assign a cost for a disallowed transition'

    # Success

def test_schedule_engagement():
    """Test the schedule_engagement method for a LocalAsset.
    
    Tests engagement scheduling across time intervals.
    
    CASES:
    1. Generate engagement schedule for two time intervals
    2. Adding a third time interval (verifies dynamic updates)
    """

    # Create test node
    test_node = MockTN()

    # Create Market with modern API
    test_mkt = make_market(test_node, datetime.now(timezone.utc), intervals_to_clear=2)

    # Establish test model that is a LocalAsset with modern API
    test_model = LocalAsset(transactive_node=test_node)
    test_node.local_assets = [test_model]

    # Run the first test case
    test_model.schedule_engagement(test_mkt)
    # Were the right number of engagement schedule values created?
    # In modern API, engagement_schedule is an IntervalSeries, check each time interval
    assert test_mkt.time_intervals.number_of_intervals == 2, 'Market should have 2 time intervals'
    
    engagement_values = []
    for ti in test_mkt.time_intervals:
        val = test_model.engagement_schedule.get_by_interval(ti)
        if val is not None:
            engagement_values.append(val)
    
    assert len(engagement_values) == 2, 'The method did not store the engagement schedule'

    # Were the correct scheduled engagement values stored?
    assert all([val == 1 for val in engagement_values]), \
        'The stored engagement schedule was not as expected'

    # Create and store another active time interval by creating a new market with 3 intervals
    test_mkt = make_market(test_node, datetime.now(timezone.utc), intervals_to_clear=3)

    # Run next test case
    test_model.schedule_engagement(test_mkt)
    # Was the new time interval used?
    assert test_mkt.time_intervals.number_of_intervals == 3, 'Market should have 3 time intervals'
    
    engagement_values = []
    for ti in test_mkt.time_intervals:
        val = test_model.engagement_schedule.get_by_interval(ti)
        if val is not None:
            engagement_values.append(val)
    
    assert len(engagement_values) == 3, 'The method apparently failed to create a new engagement'

    # Were the existing time interval values reassigned properly?
    assert all([val == 1 for val in engagement_values]), \
        'The existing list was not augmented as expected'

    # Success


def test_schedule_power():
    """Test the schedule_power method for a LocalAsset.
    
    Tests power scheduling across time intervals.
    
    CASES:
    1. Power is scheduled for two time intervals
    2. Power is scheduled for three time intervals (verifies dynamic updates)
    """

    # Create test node
    test_node = MockTN()

    # Create Market with modern API
    test_mkt = make_market(test_node, datetime.now(timezone.utc), intervals_to_clear=2)

    # Establish test model that is a LocalAsset with a default power property
    test_model = LocalAsset(transactive_node=test_node)
    test_node.local_assets = [test_model]
    test_model.default_power = 3.14159

    # Run the first test case
    test_model.schedule_power(test_mkt)
    # Were the right number of scheduled power values created?
    assert test_mkt.time_intervals.number_of_intervals == 2, 'Market should have 2 time intervals'
    
    scheduled_power_values = []
    for ti in test_mkt.time_intervals:
        val = test_model.scheduled_powers.get_by_interval(ti)
        if val is not None:
            scheduled_power_values.append(val)
    
    assert len(scheduled_power_values) == 2, 'The method did not store the right number of results'

    # Were the correct scheduled power values stored?
    assert all([val == test_model.default_power for val in scheduled_power_values]), \
        'The stored scheduled powers were not as expected'

    # Change the default power
    test_model.default_power = 6

    # Create and store another active time interval by creating a new market with 3 intervals
    test_mkt = make_market(test_node, datetime.now(timezone.utc), intervals_to_clear=3)

    # Run next test case
    test_model.schedule_power(test_mkt)
    # Was the new time interval used?
    assert test_mkt.time_intervals.number_of_intervals == 3, 'Market should have 3 time intervals'
    
    scheduled_power_values = []
    for ti in test_mkt.time_intervals:
        val = test_model.scheduled_powers.get_by_interval(ti)
        if val is not None:
            scheduled_power_values.append(val)
    
    assert len(scheduled_power_values) == 3, 'The method failed to create a new scheduled power'

    # Were the existing time intervals reassigned properly?
    assert all([val == test_model.default_power for val in scheduled_power_values]), \
        'existing scheduled powers were not reassigned properly'

    # Success


def test_update_dual_costs():
    """Test the update_dual_costs method for a LocalAsset.
    
    This method creates or revises dual costs in active time intervals using
    active vertices, scheduled powers, and marginal prices.
    
    NOTE: This test is virtually identical to the Neighbor test of the same name.
    
    CASES:
    1. First calculation of a dual cost
    2. Reassignment of an existing dual cost
    """

    # Create test node
    test_node = MockTN()

    # Create Market with modern API
    test_market = make_market(test_node, datetime.now(timezone.utc))

    # Get the time interval created by Market
    time_interval = test_market.time_intervals[0]

    # Create and store a marginal price using modern API
    test_market.marginal_prices.update(time_interval, 0.1)

    # Create a test LocalAsset
    test_model = LocalAsset(transactive_node=test_node)
    test_node.local_assets = [test_model]

    # Create and store a scheduled power in the active time interval
    test_model.scheduled_powers.update(time_interval, 100)

    # Create and store a production cost in the active time interval
    test_model.production_costs.update(time_interval, 1000)

    # TEST 1

    test_model.update_dual_costs(test_market)
    dual_cost = test_model.dual_costs.get_by_interval(time_interval)
    assert dual_cost is not None, 'A dual cost should have been created'
    assert dual_cost == (1000 - 100 * 0.1), 'An unexpected dual cost value was found'

    # TEST 2

    # Configure the test by modifying the marginal price value
    test_market.marginal_prices.update(time_interval, 0.2)

    test_model.update_dual_costs(test_market)
    dual_cost = test_model.dual_costs.get_by_interval(time_interval)
    assert dual_cost is not None, 'A dual cost should exist'
    assert dual_cost == (1000 - 100 * 0.2), 'An unexpected dual cost value was found'

    # Success


def test_update_production_costs():
    """Test the update_production_costs method for a LocalAsset.
    
    This method calculates production costs from active vertices and scheduled powers.
    
    NOTE: This test is virtually identical to the Neighbor test of the same name.
    
    CASES:
    1. First calculation of a production cost
    2. Reassignment of an existing production cost
    """

    # Create test node
    test_node = MockTN()

    # Create Market with modern API
    test_market = make_market(test_node, datetime.now(timezone.utc))

    # Get the time interval created by Market
    time_interval = test_market.time_intervals[0]

    # Create a test LocalAsset
    test_model = LocalAsset(transactive_node=test_node)
    test_node.local_assets = [test_model]

    # Create and store a scheduled power in the active time interval
    test_model.scheduled_powers.update(time_interval, 50)

    # Create and store some active vertices using SDCurve
    vertices = [
        Vertex(0.1, 1000, 0),
        Vertex(0.2, 1015, 100)
    ]
    sd_curve = SDCurve(measurement=None, interval=time_interval, vertices=vertices)
    test_model.active_vertices.update(time_interval, sd_curve)

    # TEST 1

    test_model.update_production_costs(test_market)
    production_cost = test_model.production_costs.get_by_interval(time_interval)
    assert production_cost is not None, 'A production cost should have been created'
    # The integral of the linear marginal price between the bracketing vertices (half factor on the quadratic term).
    assert production_cost.value == pytest.approx(1006.25), 'An unexpected production cost value was found'

    # TEST 2

    # Configure the test by modifying the scheduled power value
    test_model.scheduled_powers.update(time_interval, 150)

    test_model.update_production_costs(test_market)
    production_cost = test_model.production_costs.get_by_interval(time_interval)
    assert production_cost is not None, 'A production cost should exist'
    assert production_cost == 1015, 'An unexpected dual cost value was found'

    # Success


def test_update_vertices():
    """Test method update_vertices(), which for this base class of LocalAsset does
    practically nothing and must be redefined by child classes that represent flexible assets."""

    # Create test node
    test_node = MockTN()

    # Define a simple market series helper class
    test_market = make_market(test_node, datetime.now(timezone.utc))
    
    # Get the first time interval
    time_interval = test_market.time_intervals[0]
    
    # Create a test LocalAsset
    test_model = LocalAsset(transactive_node=test_node)
    test_node.local_assets = [test_model]
    
    # Create and store a scheduled power in the active time interval
    test_model.scheduled_powers.update(time_interval, 50)
    
    # Set maximum and minimum powers
    test_model.maximum_power = 200
    test_model.minimum_power = 0

    # TEST 1

    test_model.update_vertices(test_market)
    assert len(list(test_model.active_vertices.values)) == 1, \
        'There were an unexpected number of active vertices'

    # Success.
