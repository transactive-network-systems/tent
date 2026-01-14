import unittest
from bellman import Bellman
import unittest


tc = unittest.TestCase()


def insert_header():
    print("\n-0000000-")


def ran():
    print("  - Ran to completion.")


def failed(error):
    print('- THE TEST FAILED:', error)


def example_cost_function(
        initial_state,
        control_action,
        minimum_state,
        maximum_state
):
    cost: float = 10
    return cost


class TestBellman:

    def __init__(self):
        self.test_object = None
        self.assertEqual = unittest.TestCase.assertEqual

    def setUp(self):
        self.test_object = Bellman(
            minimum_state=0,
            maximum_state=100,
            minimum_control=0,
            maximum_control=100,
            num_control_states=101,
            transition_cost_function=example_cost_function,
            num_states=1,
            prohibitive_cost=1e3
        )
        self.test_object.transitionCostFunction = example_cost_function
        return

    def test_calculate_viable_control_states(self):
        print("  Case 1: Simplest case without passing parameters")
        self.setUp()
        try:
            viable_control_states = self.test_object.calculate_viable_control_states()
            ran()
        except RuntimeError as error:
            failed(error)
            viable_control_states = None
        tc.assertEqual(min(viable_control_states), self.test_object.minControl,
                       "The minimum discrete control value is wrong.")
        tc.assertEqual(max(viable_control_states), self.test_object.maxControl,
                       "The maximum discrete control value is wrong.")
        tc.assertEqual(len(viable_control_states), self.test_object.numControlStates,
                       "The number of discrete control states is wrong.")

        print("  Case 2: Minimum control value is not a floating or integer value")
        self.setUp()
        try:
            viable_control_states = self.test_object.calculate_viable_control_states(
                minimum_control_state_value="not a floating point value",
            )
            ran()
        except RuntimeError as error:
            failed(error)
        tc.assertEqual(min(viable_control_states), self.test_object.minControl,
                       "The minimum discrete control value is wrong.")

        print("  Case 3: Maximum control value is not a floating or integer value")
        self.setUp()
        try:
            viable_control_states = self.test_object.calculate_viable_control_states(
                maximum_control_state_value="not a floating point value",
            )
            ran()
        except RuntimeError as error:
            failed(error)
            tc.assertEqual(max(viable_control_states), self.test_object.maxControl,
                           "The maximum discrete control value is wrong.")

        print("  Case 4: Number of discrete control states is not an integer")
        self.setUp()
        try:
            viable_control_states = self.test_object.calculate_viable_control_states(
                number_of_discrete_control_states="not an integer"
            )
            ran()
        except RuntimeError as error:
            failed(error)

        print("  Case 5: Check calulation with proper passed parameters")
        self.setUp()
        try:
            viable_control_states = self.test_object.calculate_viable_control_states(
                minimum_control_state_value=-50,
                maximum_control_state_value=50,
                number_of_discrete_control_states=101
            )
            ran()
        except RuntimeError as error:
            failed(error)
            viable_control_states = None
        tc.assertEqual(min(viable_control_states), -50,
                       "The minimum discrete control value is wrong.")
        tc.assertEqual(max(viable_control_states), 50,
                       "The maximum discrete control value is wrong.")
        tc.assertEqual(len(viable_control_states), 101,
                       "The number of discrete control states is wrong.")

        return

    def test_find_future_costs(self):
        self.setUp()
        prices = [0, 1, 2]
        trajectory_cost_matrix = self.test_object.trajectoryCostMatrix = Bellman.initialize_cost_matrix(
            num_control_states=2,
            num_states=1,
            num_intervals=len(prices)
        )
        self.test_object.find_future_costs(
            initial_state=0,  # current state (at beginning of trajectory)
            prices=prices,  # price trajectory
            trajectory_cost_matrix=trajectory_cost_matrix,  # initial control state trajectory
        )

    def test_find_minimum_cost(self):
        self.setUp()
        minimum_cost = self.test_object.find_minimum_cost(
            trajectory_cost_matrix=self.test_object.trajectoryCostMatrix
        )

    def test_find_optimal_trajectory(self):
        self.setUp()
        optimal_trajectory = self.test_object.find_optimal_trajectory(
            trajectory_cost_matrix=self.test_object.trajectoryCostMatrix
        )

    def test_initialize_control_state_trajectory(self):
        print("  Case 1: Simplest case without passing parameters")
        self.setUp()
        tc.assertEqual(self.test_object.minControl, 0.0,
                       "The prior minimum control action was expected to be 0.0")
        tc.assertEqual(self.test_object.maxControl, 100.0,
                       "The prior maximum control action was expected to be 100.0")
        tc.assertEqual(self.test_object.numControlStates, 101,
                       "The number of discrete control states was expected to be 101")
        try:
            initialized_state_trajectory = self.test_object.initialize_control_state_trajectory()
            ran()
        except RuntimeError as error:
            failed(error)
            initialized_state_trajectory = None
        tc.assertEqual(initialized_state_trajectory[0], 50.0,
                       "The initialized control value is wrong")
        tc.assertEqual(len(initialized_state_trajectory), 1,
                       "The wrong number of time intervals were initialized")

        print("  Case 2: Controls initialized for more than one time interval")
        self.setUp()
        try:
            initialized_state_trajectory = self.test_object.initialize_control_state_trajectory(
                num_intervals=10)
            ran()
        except RuntimeError as error:
            failed(error)
            initialized_state_trajectory = None
        tc.assertEqual(initialized_state_trajectory[0], 50.0,
                       "The initialized control value is wrong")
        tc.assertEqual(len(initialized_state_trajectory), 10,
                       "The wrong number of time intervals were initialized")

        print("  Case 3: With new discrete states being passed as an argument")
        self.setUp()
        try:
            initialized_state_trajectory = self.test_object.initialize_control_state_trajectory(
                viable_control_states=[0.0, 2.0, 4.0, 6.0],
                num_intervals=10
            )
            ran()
        except RuntimeError as error:
            failed(error)
            initialized_state_trajectory = None
        tc.assertEqual(initialized_state_trajectory[0], 2.0,
                       "The initialized control value is wrong")
        tc.assertEqual(len(initialized_state_trajectory), 10,
                       "The wrong number of time intervals were initialized")

    def test_initialize_cost_matrix(self):
        self.setUp()
        Bellman.initialize_cost_matrix(
            num_control_states=2,
            num_states=1,
            num_intervals=1
        )

    def test_update_flexibility_vertices(self):
        self.setUp()
        updated_flexibility_vertices = self.test_object.update_flexibility_vertices(
            trajectory_cost_matrix=self.test_object.trajectoryCostMatrix
        )

    def test_update_forward_costs(self):
        self.setUp()
        initialized_cost_matrix = self.test_object.initialize_cost_matrix(
            num_control_states=2,
            num_states=1,
            num_intervals=1
        )


if __name__ == '__main__':
    insert_header()
    print("Initializing Module TestBellman.")
    test_fixture = TestBellman()

    insert_header()
    print("Running test_calculate_viable_control_states()")
    test_fixture.test_calculate_viable_control_states()
    print("Method test_calculate_viable_control_states() ran to completion")

    # insert_header()
    # print("Running test_find_future_costs()")
    # test_fixture.test_find_future_costs()
    # print("Method test_find_future_costs() ran to completion")

    # insert_header()
    # print("Running test_find_minimum_cost()")
    # test_fixture.test_find_minimum_cost()
    # print("Method test_find_minimum_cost() ran to completion")

    # insert_header()
    # print("Running test_find_optimal_trajectory()")
    # test_fixture.test_find_optimal_trajectory()
    # print("Method test_find_optimal_trajectory() ran to completion")
    #
    insert_header()
    print("Running test_initialize_control_state_trajectory()")
    test_fixture.test_initialize_control_state_trajectory()
    print("Method test_initialize_control_state_trajectory() ran to completion")
    #
    # insert_header()
    # print("Running test_initialize_cost_matrix()")
    # test_fixture.test_initialize_cost_matrix()
    # print("Method test_initialize_cost_matrix() ran to completion")
    #
    # insert_header()
    # print("Running test_update_forward_costs()")
    # test_fixture.test_update_forward_costs()
    # print("Method test_update_forward_costs() ran to completion")
    #
    # insert_header()
    # print("Running test_update_flexibility_vertices()")
    # test_fixture.test_update_flexibility_vertices()
    # print("Method test_update_flexibility_vertices() ran to completion")
    #
    # insert_header()
