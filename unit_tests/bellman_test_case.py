import unittest
from bellman import Bellman


class BellmanTestCase(unittest.TestCase):

    def setUp(self):

    def test_something(self):
        self.assertEqual(True, False)
        Bellman(initial_state,              # current state
                state_range,                # minimum and maximum state values in the state trajectory
                control_state_range,        # minimum and maximum control state values in trajectory
                num_control_states,         # number of discrete control states that the viable range should be divided into
                prices,                     # price trajectory
                transition_cost_function,   # function name that defines the cost between successive states
                control_state_trajectory=None,  # current control state + initial control state trajectory
                find_flexibility=False,     # Calculate flexibiltiy if true
                inf_cost_magnitude=float('inf'),  # cost magnitude that is prohibitive, assigned infinity
                **kwargs                    # list of transition function arguments
                )



if __name__ == '__main__':
    unittest.main()
