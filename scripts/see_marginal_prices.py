from matplotlib import pyplot as plt

def see_marginal_prices(market, show=True):
    """
    Visualize this market's current marginal prices for its active time intervals.
    This should remain independent from any implementation platform, which probably means it must be called by an
    active display interface.
    :param market: [Market] The market to visualize
    :param show: [Boolean] Set False to not show figure (useful for testing)
    :return:
    """

    start_times, marginal_prices = zip(*[(k.start_time, v.value) for k, v in market.marginal_prices.all])
    marginal_prices.append(marginal_prices[-1])
    start_times.append(start_times[-1] + market.series.duration.market_interval)

    fig, ax = plt.subplots()
    ax.step(start_times, marginal_prices, where='post')

    ax.set(xlabel='interval starting times',
           ylabel='marginal prices [$/kWh]',
           title=(market.name + ' Marginal Prices')
           )
    ax.grid()

    if show is True:
        plt.show()