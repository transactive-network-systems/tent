"""
function Bellman
Finds an optimal set of control states for a trajectory of decision time periods. This function is originally drafted
to help schedule local assets when given a forward price trajectory.
Don Hammerstrom, PNNL
May 2022
"""




def cost_function(start_state, control, price, kwargs):
    """Placeholder transition-cost function (the original cost_function module): a constant end state and cost. A
    real cost function comes with the model-frame scheduler integration (planning/THERMAL_ZONE_AND_SCHEDULER_PLAN.md)."""
    end_state = .5
    transition_cost = 7
    return end_state, transition_cost


class Bellman():
    def __init__(self,
                 minimum_state=0,  # minimum and maximum state values in the state trajectory
                 maximum_state=1,
                 minimum_control=0,
                 maximum_control=1,
                 num_control_states=5,  # number of discrete control states that the viable range should be divided into
                 transition_cost_function=cost_function,  # function that defines the cost between successive states
                 num_states=1,  # number of system states
                 prohibitive_cost=1e6  # Cost that will be considered prohibitive in flexibility range
                 ):
        self.minState = minimum_state
        self.maxState = maximum_state
        self.numStates = num_states
        self.minControl = minimum_control
        self.maxControl = maximum_control
        self.numControlStates = num_control_states
        self.transitionCostFunction = transition_cost_function
        self.prohibitiveCost = prohibitive_cost

        self.trajectoryCostMatrix = []
        self.optimalTrajectory = []
        self.flexibilityVertices = []
        self.viableControlStates = self.calculate_viable_control_states(
            minimum_control_state_value=self.minControl,
            maximum_control_state_value=self.maxControl,
            number_of_discrete_control_states=self.numControlStates)

    def update_forward_costs(self,
                             initial_state: float = 0,  # current state (at beginning of trajectory)
                             trajectory_cost_matrix=None,
                             prices: list = None,  # price trajectory
                             ):
        if trajectory_cost_matrix:
            trajectory_cost_matrix = self.trajectoryCostMatrix
        NI = len(trajectory_cost_matrix[0])  # number of price intervals
        NCS: int = round(len(trajectory_cost_matrix[0][0])/4)  # number of control states
        kwargs = None
        for i in range(NI):
            for j in range(NCS):  # iterate through viable source control states
                for k in range(NCS):  # iterate through viable target control states
                    if i == 0:  # The initial price interval.
                        end_state, transition_cost = self.transitionCostFunction(
                            initial_state,
                            self.viableControlStates[k],
                            prices[i],  # interval price
                            kwargs)  # other arguments
                        # Save the transition information that results in the minimum cumulative forward cost.
                        if transition_cost < trajectory_cost_matrix[i][k][2]:
                            trajectory_cost_matrix[i][k][0] = j  # source control state
                            trajectory_cost_matrix[i][k][1] = end_state  # resulting state
                            trajectory_cost_matrix[i][k][2] = transition_cost  # initial cumulative forward transition cost
                    else:  # Not the initial price interval.
                        end_state, transition_cost = self.transitionCostFunction(
                            trajectory_cost_matrix[i - 1][j][1], # source states
                            self.viableControlStates[k],  # control
                            prices[i],  # interval price
                            kwargs)  # other arguments
                        # Save the transition information that results in the minimum cumulative forward cost.
                        if trajectory_cost_matrix[i - 1][j][2] + transition_cost < trajectory_cost_matrix[i][k][2]:
                            trajectory_cost_matrix[i][k][0] = j
                            trajectory_cost_matrix[i][k][1] = end_state
                            trajectory_cost_matrix[i][k][2] = trajectory_cost_matrix[i - 1][j][2] + transition_cost
        return trajectory_cost_matrix

    def find_minimum_cost(self, trajectory_cost_matrix=None):
        """
        find_optimal_trajectory: Use the trajectory cost matrix to find the optimal (i.e., minimal cost) state
                                 trajectory.
        :param trajectory_cost_matrix: [nested lists of floats]: [interval [control state]] Matrix of cumulative
                                       transition costs under viable control state trajectories.
        :return: optimal_trajectory: [list of floats] The trajectory of control states that minimizes total trajectory
                                     costs.
        """
        if not trajectory_cost_matrix:
            if not self.trajectoryCostMatrix:
                trajectory_cost_matrix = self.initialize_cost_matrix(
                    num_states=self.numStates,
                    num_control_states=self.numControlStates,
                    num_intervals=1  #todo: fix this. What if these counts are not defined?
                )
            else:
                trajectory_cost_matrix = self.trajectoryCostMatrix
        minimum_cost = min([x[2] for x in trajectory_cost_matrix[-1][:]])
        return minimum_cost

    def find_optimal_trajectory(self, trajectory_cost_matrix=None):
        """
        find_optimal_trajectory: Use the trajectory cost matrix to find the optimal (i.e., minimal cost) state
                                 trajectory.
        :param trajectory_cost_matrix: [nested lists of floats]: [interval [control state]] Matrix of cumulative
                                       transition costs under viable control state trajectories.
        :return: optimal_trajectory: [list of floats] The trajectory of control states that minimizes total trajectory
                                     costs.
        """
        if not trajectory_cost_matrix:
            if not self.trajectoryCostMatrix:
                trajectory_cost_matrix = self.initialize_cost_matrix(
                    num_control_states=self.numControlStates,
                    num_states=self.numStates,
                    num_intervals=1  # todo: improve this. How to infer numbers of time intervals.
                )
            else:
                trajectory_cost_matrix = self.trajectoryCostMatrix
        # The number of time intervals is defined by the number of price intervals in the trajectory cost matrix.
        # NI = len(trajectory_cost_matrix[0])  # Number of time intervals todo: Fix this indexing problem
        NI = 1
        try:
            NCS = len(trajectory_cost_matrix[0][0])  # Number of viable control states todo: fix this indexing problem
        except:
            NCS = self.numControlStates
        minimum_cost = min([x[2] for x in trajectory_cost_matrix[-1][:]])
        optimal_trajectory = NI * [-1]
        optimal_trajectory[-1] = [i for i in range(NCS)
                                  if trajectory_cost_matrix[-1][i][2] == minimum_cost][0]
        for i in range(NI - 1, 0, -1):
            optimal_trajectory[i] = trajectory_cost_matrix[i + 1][optimal_trajectory[i + 1]][0]
        return optimal_trajectory, minimum_cost

    def update_flexibility_vertices(self,
                                    trajectory_cost_matrix: object = None
                                    ):
        """
        NOTE: Presume that trajectory cost matrix has been fully populated with cumulative past costs (Entry 2) and
              future costs (Entry 3).
        :param trajectory_cost_matrix:
        :return: flexibility_vertices: Price-quantity pairs (at all or at selected time interval??)
        """
        flexibility_vertices = []
        if not trajectory_cost_matrix:
            trajectory_cost_matrix = self.trajectoryCostMatrix
        return flexibility_vertices

    def calculate_viable_control_states(
            self,
            minimum_control_state_value: float = None,
            maximum_control_state_value: float = None,
            number_of_discrete_control_states: int = None
            ):
        """
        This method divides a viable control action range into discrete set of equally spaced control states.
        :param minimum_control_state_value: [float] Minimum allowed control value
        :param maximum_control_state_value: [float] Maximum allowed control value
        :param number_of_discrete_control_states: [int] Number of equally-spaced discrete control states.
        :return: [list of floats] List of viable control states.
        """
        # Check input minimum control state value:
        if isinstance(minimum_control_state_value, int):
            minimum_control_state_value = float(minimum_control_state_value)
        if not minimum_control_state_value or not isinstance(minimum_control_state_value, float):
            if not self.minControl:
                minimum_control_state_value = 0.0
            else:
                minimum_control_state_value = self.minControl

        # Check input maximum control state value:
        if isinstance(maximum_control_state_value, int):
            maximum_control_state_value = float(maximum_control_state_value)
        if not maximum_control_state_value or not isinstance(maximum_control_state_value, float):
            if not self.maxControl:
                maximum_control_state_value = 1.0
            else:
                maximum_control_state_value = self.maxControl

        # Check input number of discrete control states:
        if isinstance(number_of_discrete_control_states, float):
            number_of_discrete_control_states = int(number_of_discrete_control_states)
        if not number_of_discrete_control_states or not isinstance(number_of_discrete_control_states, int):
            if not self.numControlStates:
                number_of_discrete_control_states = 2
            else:
                number_of_discrete_control_states = self.numControlStates

        viable_control_states: list = [minimum_control_state_value
                                 + x * (maximum_control_state_value - minimum_control_state_value)
                                 / (number_of_discrete_control_states - 1)
                                 for x in range(number_of_discrete_control_states)]
        return viable_control_states

    def initialize_control_state_trajectory(self,
                                            viable_control_states=None,
                                            num_intervals=None):
        """
        initialize_control_state_trajectory: Initialize the control state trajectory.
        :param viable_control_states: List of discrete control states.
        :param num_control_states: [int] Number of discrete control states
        :param num_intervals: [int]: Number of price time intervals
        :return: List of initialized control states for the time intervals.
        """
        # Check for valid discrete set of control states
        if not viable_control_states:
            if not self.viableControlStates:
                viable_control_states = self.calculate_viable_control_states(
                    minimum_control_state_value=self.minControl,
                    maximum_control_state_value=self.maxControl,
                    number_of_discrete_control_states=self.numControlStates
                )
            else:
                viable_control_states = self.viableControlStates

        num_control_states = len(viable_control_states)

        # Check the number of time intervals
        if isinstance(num_intervals, float):
            num_intervals = int(num_intervals)
        if not num_intervals:
            if not self.trajectoryCostMatrix:
                num_intervals = 1
            else:
                num_intervals = len(self.trajectoryCostMatrix[0])

        initialized_state_trajectory = [viable_control_states[int((num_control_states - 1) / 2)]
                                        for x in range(num_intervals)]

        return initialized_state_trajectory

    def find_future_costs(self,
                          initial_state: float,  # current state (at beginning of trajectory)
                          prices=None,  # price trajectory
                          trajectory_cost_matrix=None,  # initial control state trajectory
                          **kwargs):  # list of transition function arguments
        # Set backward-looking costs to 0 in last price interval. (?)
        if not trajectory_cost_matrix:
            if not self.trajectoryCostMatrix:
                trajectory_cost_matrix = self.initialize_cost_matrix(
                    num_states=self.numControlStates,
                    num_control_states=self.numControlStates,
                    num_intervals=1
                )
            else:
                trajectory_cost_matrix = self.trajectoryCostMatrix
        NI: int = len(trajectory_cost_matrix[0])  # Number of time intervals
        NCS: int = len(trajectory_cost_matrix[0][0])  # Number of viable control states
        for k in range(NCS):
            # trajectory_cost_matrix[-1][k][3] = 0 todo: fix this indexing problem
            pass
            # TODO: find the rest of the future cost sums (e.g., via multiple forward calculations!)
        return  # return what??

    @classmethod
    def initialize_cost_matrix(cls,
                               num_control_states=2,
                               num_states=1,
                               num_intervals=1):
        entry = [
            -1,  # prior optimal control state index
            [-1 for x in range(num_states)],  # ending state(s)
            float('inf'),  # minimum cumulative transition costs up to this point
            float('inf')  # minimum cumulative transition costs from this point forward
        ]
        cost_matrix = [[entry[:] for i in range(num_control_states)] for j in range(num_intervals)]
        return cost_matrix

    # # Iterate forward again from each control matrix point to calculate its forward costs.
    # # todo: This can be done with the forward methods from each alternative control state and interval. It does not
    # #       therefore need to be an independent calculation.
    # for i in range(N - 1):  # iterate through forward price intervals
    #     for m in range(i, N-1):
    #         for j in range(len(viable_control_states)):  # iterate through viable source control states
    #             temp_entry = [-1,  # opt. prior control state
    #                           [0 for x in range(num_states)],  # states
    #                           float('inf')]  # cumulative trajectory cost
    #             temp_matrix = [[temp_entry[:] for x in range(len(viable_control_states))] for y in range(i, N - 1)]
    #             for k in range(len(viable_control_states)):  # iterate through viable target control states
    #                 if m == 0:
    #                     end_state, transition_cost = transition_cost_function(control_matrix[m][j][1],  # source
#                     states
    #                                                                     viable_control_states[k],  # control
    #                                                                     prices[m],  # interval price
    #                                                                     kwargs)  # other arguments
    #                     # Save the transition information that results in the minimum cumulative forward cost.
    #                     if control_matrix[m][j][2] + transition_cost < temp_matrix[m][k][2]:
    #                         temp_matrix[m][k][0] = j
    #                         temp_matrix[m][k][1] = end_state
    #                         temp_matrix[m][k][2] = control_matrix[m][j][2] + transition_cost
    #                 else:
    #                     end_state, transition_cost = transition_cost_function(temp_matrix[m-1][j][1],  # source states
    #                                                                       viable_control_states[k],  # control
    #                                                                       prices[m-1],  # interval price
    #                                                                       kwargs)  # other arguments
    #                     # Save the transition information that results in the minimum cumulative forward cost.
    #                     print(i,m,j,k)
    #                     if temp_matrix[m-1][j][2] + transition_cost < temp_matrix[m][k][2]:
    #                         temp_matrix[m][k][0] = j
    #                         temp_matrix[m][k][1] = end_state
    #                         temp_matrix[m][k][2] = temp_matrix[m-1][j][2] + transition_cost
    #
    #             control_matrix[i][j][3] = min([x[2] for x in temp_matrix[-1][:]])
    #
    # # 6. Choose best trajectory from the alternatives
    # trajectory_cost = min([x[2] for x in control_matrix[-1][:]])
    # control_trajectory = N * [-1]
    # control_trajectory[N-1] = [i for i in range(len(viable_control_states))
    #                            if control_matrix[N-1][i][2] == trajectory_cost][0]
    # for i in range(N-1, -1, -1):
    #     control_trajectory[i] = control_matrix[i+1][control_trajectory[i+1]][0]
    #
    # # 11. If find_flexibility is true, estimate flexibility bids or offers. (Consider back calculation from the next
# point
    # #     in the optimal trajectory to estimate the cost difference locally in the period) (An alternative is to use
    # #     perturbation on the optimal trajectory. To do so, alter the control variable in the time interval of
# interest,
    # #     and adjust the perturbed electricity price so that the sum trajectory costs of electricity and utility
# remain unchanged.)
    # # if find_flexibility:
    # #     for i in range(N):
    # #         for j in range(control_trajectory[i]):
    # #             if control_matrix[i][j][2] < prohibitive_cost:
    # #                 dCost = control_matrix[i][j][2] - control_matrix[i][control_trajectory[i]][2]
    # #                 dPower = viable_control_states[control_matrix[i][j][1]] - \
    # #                          viable_control_states[control_matrix[i][control_trajectory[i]][1]]
    # #                 if dPower == 0:
    # #                     flexibility[i].append(viable_control_states[control_trajectory[j]], 0)
    # #                 else:
    # #                     flexibility[i].append(viable_control_states[control_trajectory[j]], dCost / dPower)
    #
    # return  # control_state_trajectory, state_trajectory, marginal_costs, vertices, trajectory_cost, status


if __name__ == "__main__":
    Bellman(minimum_state=0, maximum_state=1, minimum_control=0, maximum_control=1, num_control_states=5,
            num_states=1)
