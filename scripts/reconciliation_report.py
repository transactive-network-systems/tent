import os


def reconciliation_report(market):
    # Save market object data:
    # TODO: This report was previously saved by Market.transition_from_reconcile_to_expired().
    #    It does not make sense to save a CSV file for each market object, and we now have a database.
    #    If this report is useful, it should be refactored to use the database instead.
    # Create a table for market object data.
    data = []
    # Append data for active market vertices:
    for interval in market.time_intervals:
        for vertex in market.active_vertices.get_by_interval(interval).vertices:
            data.append([market.name, interval.name, vertex.price, vertex.quantity])
        # Append data for local assets & neighbors:
        for entity in market.tn.local_assets + market.tn.neighbors:
            for vertex in entity.active_vertices.get_by_interval(interval).vertices:
                data.append([entity.name, interval.name, vertex.price, vertex.quantity])
    # Write the vertex data into a csv file based on the current working directory.
    import csv
    from pathlib import Path
    data_folder = Path(os.getcwd(), '..', 'Market_Data')
    with open(data_folder / f'{market.series.name}.csv', 'w+') as f:
        writer = csv.writer(f)
        writer.writerows(data)
    # Gather simpler marginal price data:
    price_data = []
    for time_interval, marginal_price in market.marginal_prices.all:
        price_data.append([market.name, time_interval.start_time, marginal_price.value])
    with open(data_folder / (market.name.replace(':', '_') + ".csv"), 'w+') as f:
        writer = csv.writer(f)
        writer.writerows(price_data)
