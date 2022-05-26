"""
function Bellman
Finds an optimal set of control states for a trajectory of decision time periods. This function is originally drafted
to help schedule local assets when given a price trajectory.
Don Hammerstrom, PNNL
May 2022
"""


def Bellman(initial_state,              # current state
            state_range,                # minimum and maximum state values in the state trajectory
            control_state_range,        # minimum and maximum control state values in trajectory
            num_control_states,         # number of discrete control states that the viable range should be divided into
            prices,                     # price trajectory
            transition_cost_function,   # function name that defines the cost between successive states
            control_state_trajectory=None,  # current control state + initial control state trajectory
            find_flexibility=False,     # Calculate flexibiltiy if true
            inf_cost_magnitude=float('inf'),  # cost magnitude that is prohibitive, assigned infinity
            **kwargs                    # list of transition function arguments
            ):

    status = 1
    cost = None
    N = len(prices)  # The number of time intervals is defined by the number of price intervals, at least for now.

    # 1. Check inputs
    #     1a. check number of periods for consistency between states and control states, and costs
    #     1d. make sure the transition cost function can be located

    # Item 2 is removed. The states do not need to be discretized in this formulation.

    # 3. Create discrete viable control list
    viable_control_states = [min(control_state_range) +
                             x * (max(control_state_range) - min(control_state_range)) / (num_control_states - 1)
                             for x in range(num_control_states)]

    # 4. create trajectory paths. I had done this before using a very reduced set of control states near the optimal
    #    trajectory, i.e., [1-, 0 , 1+]. I think that keeping track of a viable states and their past and future optimal
    #    costs might facilitate the estimation of flexibiltiy, however. (Maybe the decision can depend on whether the
    #    flexibiltiy is needed?)
    # NOTE: Combine this with the next item. It may not be necessary to pre-establish the control trajectory.

    # 5. calculate cumulative trajectory costs for each transition using the provided function (keep track of prior state)
    # This allows for receiving a reasonable or optimal control trajectory as the starting point.
    if control_state_trajectory is None:
        control_state_trajectory = [viable_control_states[round(num_control_states / 2)] for x in range(N)]

    # Initialize the control result matrix. This allows the range of viable control states to be considered at each
    # price interval. NOTE: This initialization must be done carefully to make sure the matrix is not populated by
    # object references.
    entry = [0,             # prior control state
             0,             # forward transition cost
             float('inf'),  # cumulative forward transition cost to this point
             float('inf')   # cumulative reverse transition cost to this point
             ]
    control_matrix = [[entry[:] for j in range(len(viable_control_states))] for i in range(N)]

    for i in range(N):  # iterate through price intervals
        for j in range(len(viable_control_states)):  # iterate through viable source control states
            for k in range(len(viable_control_states)):  # iterate through viable target control states
                if i == 0:  # The initial price interval.
                    end_state, transition_cost = transition_cost_function(initial_state,
                                                                          viable_control_states[k],
                                                                          prices[i],  # interval price
                                                                          kwargs)  # other arguments
                    # Save the transition information that results in the minimum cumulative forward cost.
                    if transition_cost < control_matrix[i][k][2]:
                        control_matrix[i][k][0] = j  # source control state
                        control_matrix[i][k][1] = end_state  # resulting state
                        control_matrix[i][k][2] = transition_cost  # initial cumulative forward transition cost
                else:  # Not the initial price interval.
                    end_state, transition_cost = transition_cost_function(control_matrix[i-1][j][1],  # source state
                                                                          viable_control_states[k],  # control
                                                                          prices[i],  # interval price
                                                                          kwargs)  # other arguments
                    # Save the transition information that results in the minimum cumulative forward cost.
                    if control_matrix[i-1][j][2] + transition_cost < control_matrix[i][k][2]:
                        control_matrix[i][k][0] = j
                        control_matrix[i][k][1] = end_state
                        control_matrix[i][k][2] = control_matrix[i-1][j][2] + transition_cost

    # Populate the reverse costs beginning from last price interval.
    for j in range(len(viable_control_states)):
        control_matrix[N-1][j][3] = 0
    for i in range(N - 2, -1, -1):
        for k in range(len(viable_control_states)):
            for j in range(len(viable_control_states)):
                if control_matrix[i][k][1] == j:

    # 6. Choose best trajectory from the alternatives
    trajectory_cost = min([x[2] for x in control_matrix[N-1][:]])
    control_trajectory = N * [-1]
    control_trajectory[N-1] = [i for i in range(len(viable_control_states)) if control_matrix[N-1][i][2] == trajectory_cost][0]
    for i in range(N-1, -1, -1):
        pass

    # 7. return to 4, placing the optimal trajectory at the center of alternatives (consider final cost at each
    #    alternative)

    # 8. If trajectory is unchanged, stop. This is the minimum cost trajectory

    # 9. assign the minimum trajectory cost

    # 10. estimate marginal cost (internal price) of the minimum-cost trajectory at each trajectory period using the
    #     alternative control states in the trajectory period. (This is the rate of change of utility cost with change in
    #     power.)

    # 11. If find_flexibility is true, estimate flexibility bids or offers. (Consider back calculation from the next point
    #     in the optimal trajectory to estimate the cost difference locally in the period) (An alternative is to use
    #     perturbation on the optimal trajectory. To do so, alter the control variable in the time interval of interest,
    #     and adjust the perturbed electricity price so that the sum trajectory costs of electricity and utility remain unchanged.)

    return  # control_state_trajectory, state_trajectory, marginal_costs, vertices, trajectory_cost, status
