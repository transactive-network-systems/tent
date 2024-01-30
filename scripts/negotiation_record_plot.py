import matplotlib
import pandas as pd
import sqlite3

from matplotlib import pyplot as plt

print('at the beginning')
matplotlib.use('WebAgg')
print(matplotlib.get_backend())
db_file = '/home/dmr/Projects/TentWork/results/latest_db/tns.sqlite'

with sqlite3.connect(db_file) as c:
    df = pd.read_sql('select ts, json_extract(value_string, "$.full_consensus") as full_consensus, json_extract(value_string, "$.system_vertices[0].value.marginalPrice") as low_price, json_extract(value_string, "$.system_vertices[1].value.marginalPrice") as high_price, json_extract(value_string, "$.system_vertices[0].value.power") as low_power, json_extract(value_string, "$.system_vertices[1].value.power") as high_power from data d join topics t on d.topic_id = t.topic_id where topic_name = "tns/PNNL_Campus/negotiation_record" order by ts asc limit 100', c, index_col='ts')

print(df.head())
df_plot = df.loc[:, df.columns != 'full_consensus'].plot(subplots=[('high_power', 'low_power'), ('high_price', 'low_price')])
plt.show()