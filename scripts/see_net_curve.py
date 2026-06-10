from copy import deepcopy
from matplotlib import pyplot as plt

from tent.containers import Vertex

def see_net_curve(market, time_interval=None, show=True):
    """
    Visualize the net supply/demand curve in this market for a given market period.
    This should remain independent from any implementation platform, which probably means it must be called by an
    active display interface.
    :param market: [Market] The market to visualize the net supply/demand curve for.
    :param time_interval:
    :param show: [Boolean] Set False to not show figure (useful for testing)
    :return:
    """
    positive_infinity = float('Inf')
    negative_infinity = float('-Inf')
    price_extension = 0.1  # [$/kWh]

    if time_interval is None:
        time_interval = market.time_intervals[0]

    sd_curve = market.active_vertices.get_by_interval(time_interval)
    min_vertex = sd_curve.min_price_vertex
    max_vertex = sd_curve.max_price_vertex

    # Extend the curve toward negative infinity marginal price.
    if sd_curve.min_price == negative_infinity:
        min_vertex.price = -price_extension
    elif sd_curve.min_price == positive_infinity:
        sd_curve.update(Vertex(-price_extension, 0, min_vertex.quantity))
    else:
        sd_curve.update(Vertex(min_vertex.price - price_extension, 0, min_vertex.quantity))

    # Extend the curve toward positive infinity marginal price.
    if sd_curve.max_price == positive_infinity:
        max_vertex.price = price_extension
    else:
        sd_curve.update(Vertex(max_vertex.price + price_extension, 0, max_vertex.quantity))

    fig, ax = plt.subplots()
    ax.plot(sd_curve.prices, sd_curve.quantities)

    ax.set(xlabel='marginal price [$/kWh]',
           ylabel='average power [kW]',
           title=(market.name + 'Net Supply/Demand Curve ' + time_interval.name)
           )
    ax.grid()

    if show is True:
        plt.show()
