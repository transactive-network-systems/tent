"""
Sample weather data for testing.

This module provides weather forecast data as a Python data structure,
avoiding the need for CSV files that may be git-ignored.
"""

# Weather data as list of dictionaries
# Each row represents hourly weather data for 2017-07-01
WEATHER_DATA = [
    {"Time": "2017-07-01 00:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 22.5, "Pressure": 101.3, "Direction": 180, "Speed": 2.5},
    {"Time": "2017-07-01 01:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 21.8, "Pressure": 101.3, "Direction": 175, "Speed": 2.3},
    {"Time": "2017-07-01 02:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 21.2, "Pressure": 101.2, "Direction": 170, "Speed": 2.1},
    {"Time": "2017-07-01 03:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 20.8, "Pressure": 101.2, "Direction": 165, "Speed": 2.0},
    {"Time": "2017-07-01 04:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 20.5, "Pressure": 101.1, "Direction": 160, "Speed": 1.8},
    {"Time": "2017-07-01 05:00:00", "GHI": 50, "DNI": 100, "DHI": 30, "Temperature": 20.3, "Pressure": 101.1, "Direction": 155, "Speed": 1.6},
    {"Time": "2017-07-01 06:00:00", "GHI": 150, "DNI": 300, "DHI": 80, "Temperature": 21.0, "Pressure": 101.2, "Direction": 150, "Speed": 1.5},
    {"Time": "2017-07-01 07:00:00", "GHI": 300, "DNI": 500, "DHI": 120, "Temperature": 22.5, "Pressure": 101.3, "Direction": 145, "Speed": 1.8},
    {"Time": "2017-07-01 08:00:00", "GHI": 450, "DNI": 650, "DHI": 160, "Temperature": 24.2, "Pressure": 101.3, "Direction": 140, "Speed": 2.2},
    {"Time": "2017-07-01 09:00:00", "GHI": 580, "DNI": 750, "DHI": 200, "Temperature": 26.0, "Pressure": 101.4, "Direction": 135, "Speed": 2.8},
    {"Time": "2017-07-01 10:00:00", "GHI": 680, "DNI": 820, "DHI": 240, "Temperature": 27.8, "Pressure": 101.4, "Direction": 130, "Speed": 3.5},
    {"Time": "2017-07-01 11:00:00", "GHI": 750, "DNI": 850, "DHI": 260, "Temperature": 29.5, "Pressure": 101.4, "Direction": 125, "Speed": 4.0},
    {"Time": "2017-07-01 12:00:00", "GHI": 780, "DNI": 860, "DHI": 270, "Temperature": 31.0, "Pressure": 101.3, "Direction": 120, "Speed": 4.2},
    {"Time": "2017-07-01 13:00:00", "GHI": 750, "DNI": 850, "DHI": 260, "Temperature": 32.0, "Pressure": 101.3, "Direction": 115, "Speed": 4.5},
    {"Time": "2017-07-01 14:00:00", "GHI": 680, "DNI": 820, "DHI": 240, "Temperature": 32.5, "Pressure": 101.2, "Direction": 110, "Speed": 4.3},
    {"Time": "2017-07-01 15:00:00", "GHI": 580, "DNI": 750, "DHI": 200, "Temperature": 32.2, "Pressure": 101.2, "Direction": 105, "Speed": 4.0},
    {"Time": "2017-07-01 16:00:00", "GHI": 450, "DNI": 650, "DHI": 160, "Temperature": 31.5, "Pressure": 101.2, "Direction": 100, "Speed": 3.5},
    {"Time": "2017-07-01 17:00:00", "GHI": 300, "DNI": 500, "DHI": 120, "Temperature": 30.5, "Pressure": 101.3, "Direction": 95, "Speed": 3.0},
    {"Time": "2017-07-01 18:00:00", "GHI": 150, "DNI": 300, "DHI": 80, "Temperature": 29.0, "Pressure": 101.3, "Direction": 90, "Speed": 2.5},
    {"Time": "2017-07-01 19:00:00", "GHI": 50, "DNI": 100, "DHI": 30, "Temperature": 27.2, "Pressure": 101.4, "Direction": 85, "Speed": 2.0},
    {"Time": "2017-07-01 20:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 25.5, "Pressure": 101.4, "Direction": 80, "Speed": 1.8},
    {"Time": "2017-07-01 21:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 24.5, "Pressure": 101.4, "Direction": 75, "Speed": 1.6},
    {"Time": "2017-07-01 22:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 23.8, "Pressure": 101.4, "Direction": 70, "Speed": 1.5},
    {"Time": "2017-07-01 23:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 23.2, "Pressure": 101.3, "Direction": 65, "Speed": 1.4},
    {"Time": "2017-07-02 00:00:00", "GHI": 0, "DNI": 0, "DHI": 0, "Temperature": 22.8, "Pressure": 101.3, "Direction": 60, "Speed": 1.3},
]

WEATHER_CSV_HEADER = "Time,GHI,DNI,DHI,Temperature,Pressure,Direction,Speed"


def get_weather_csv_content():
    """
    Generate CSV content as a string.
    
    Returns:
        str: CSV content with header and data rows
    """
    lines = [WEATHER_CSV_HEADER]
    
    for row in WEATHER_DATA:
        line = f"{row['Time']},{row['GHI']},{row['DNI']},{row['DHI']},{row['Temperature']},{row['Pressure']},{row['Direction']},{row['Speed']}"
        lines.append(line)
    
    return '\n'.join(lines)


def write_weather_csv(file_path):
    """
    Write weather data to a CSV file.
    
    Args:
        file_path: Path where CSV should be written
    """
    with open(file_path, 'w') as f:
        f.write(get_weather_csv_content())
