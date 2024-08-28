"""
This code is taken, and refactored, and inspired from the work performed by: Kamilla Hauknes Sjursen

This method fetches the meteorological features (variables of interest), for each stake measurement available,
via the provided NETCDF-3 files fetched from the ERA5Land Reanalysis (monthly averaged) database.

Depending on the amount of variables, and the temporal scale, downloads of the climate data can take up hours.
Climate data can either be downloaded manually via the link provided in the notebook.

@Author: Julian Biesheuvel
Email: j.p.biesheuvel@student.tudelft.nl
Date Created: 21/07/2024
"""

import os
from calendar import month_abbr
import xarray as xr
import numpy as np
import pandas as pd


def get_climate_features(
    df: pd.DataFrame,
    output_fname: str,
    climate_data: str,
    geopotential_data: str,
    change_units: bool,
) -> pd.DataFrame:
    """
    Takes as input ERA5-Land monthly averaged climate data (pre-downloaded), and matches this with the locations
    of the stake measurements.

    Args:
        df (pd.DataFrame): DataFrame containing stake measurement locations and years.
        output_fname (str): Path to the output CSV file.
        climate_data (str): Path to the ERA5-Land climate data file.
        geopotential_data (str): Path to the geopotential data file.
        change_units (bool): If True, change temperature to Celsius and precipitation to m.w.e.

    Returns:
        pd.DataFrame: The updated DataFrame with climate and altitude features.
    """

    # Check if the input files exist.
    if not os.path.exists(climate_data) or not os.path.exists(geopotential_data):
        raise FileNotFoundError(f"Climate data or geopotential data do not exist.")

    # Load the two climate datasets
    ds_climate, ds_geopotential = _load_datasets(climate_data,
                                                 geopotential_data)

    # Makes things easier down the line
    # Change temperature to Celsius and precipitation to m.w.e
    if change_units:
        ds_climate["t2m"] = ds_climate["t2m"] - 273.15

    # Get latitudes and longitudes from the climate dataset.
    lat, lon = ds_climate.latitude, ds_climate.longitude

    # Convert the longitudes
    ds_180 = _adjust_longitude(ds_geopotential)

    # Crop the geopotential height to the region of interest
    ds_geopotential_cropped = _crop_geopotential(ds_180, lat, lon)

    # Calculate the geopotential height in meters
    ds_geopotential_metric = _calculate_geopotential_height(ds_geopotential_cropped)

    # Reduce expver dimension
    ds_climate = ds_climate.reduce(np.nansum, "expver")

    # Create a date range for one hydrological year
    df = _add_date_range(df)

    # Get the climate data for the latitudes and longitudes and date ranges as
    # specified
    climate_df = _process_climate_data(ds_climate, df)
    # Get the geopotential height for the latitudes and longitudes as specified,
    # for the locations of the stake measurements.
    altitude_df = _process_altitude_data(ds_geopotential_metric, df)

    # Combine the climate data with the altitude climate data
    df = _combine_dataframes(df, climate_df, altitude_df)
    # Add a new feature to the dataframe that is the height difference between the elevation
    # of the stake and the recorded height of the climate.
    df = _calculate_elevation_difference(df)

    df.to_csv(output_fname, index=False)

    return df


def _load_datasets(climate_data: str, geopotential_data: str) -> tuple:
    """Load climate and geopotential datasets."""
    with xr.open_dataset(climate_data) as dataset_climate, xr.open_dataset(
        geopotential_data
    ) as dataset_geopotential:
        return dataset_climate.load(), dataset_geopotential.load()


def _calculate_geopotential_height(ds_geopotential: xr.Dataset) -> xr.Dataset:
    """Calculate geopotential height in meters."""
    r_earth = 6367.47 * 10e3  # [m] (Grib1 radius)
    g = 9.80665  # [m/s^2]
    return ds_geopotential.assign(
        altitude_climate=lambda ds_geo: r_earth
        * (ds_geo.z / g)
        / (r_earth - (ds_geo.z / g))
    )


def _adjust_longitude(ds: xr.Dataset) -> xr.Dataset:
    """Adjust longitude coordinates to range from -180 to 180."""
    return ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180)).sortby(
        "longitude"
    )


def _crop_geopotential(
    ds: xr.Dataset, lat: xr.DataArray, lon: xr.DataArray
) -> xr.Dataset:
    """Crop geometric height to grid of climate data."""
    return ds.sel(longitude=lon, latitude=lat, method="nearest")


def _generate_climate_variable_names(ds_climate: xr.Dataset) -> list:
    """Generate list of climate variable names for one hydrological year."""
    climate_variables = list(ds_climate.keys())
    months_names = [f"_{month.lower()}" for month in month_abbr[10:] + month_abbr[1:10]]
    return [
        f"{climate_var}{month_name}"
        for climate_var in climate_variables
        for month_name in months_names
    ]


def _create_date_range(year: int):
    """Create a date range for a given year."""
    if pd.isna(year):
        return None
    year = int(year)
    return pd.date_range(start=f"{year - 1}-09-01", end=f"{year}-09-01", freq="ME")


def _add_date_range(df: pd.DataFrame) -> pd.DataFrame:
    """Add date range to DataFrame."""
    df["range_date"] = df["YEAR"].apply(_create_date_range)
    return df


def _process_climate_data(ds_climate: xr.Dataset, df: pd.DataFrame) -> pd.DataFrame:
    """Process climate data for all points and times."""

    # Create DataArrays for latitude and longitude
    lat_da = xr.DataArray(df["POINT_LAT"].values, dims="points")
    lon_da = xr.DataArray(df["POINT_LON"].values, dims="points")

    # Create a 2D array of dates ranges
    date_array = np.array([r.values for r in df["range_date"].values])
    time_da = xr.DataArray(date_array, dims=["points", "time"])

    climate_data_points = ds_climate.sel(
        latitude=lat_da,
        longitude=lon_da,
        time=time_da,
        method="nearest",
    )

    # Create a dataframe from the DataArray
    climate_df = (
        climate_data_points.to_dataframe()
        .drop(columns=["latitude", "longitude"])
        .reset_index()
    )

    # Drop columns
    climate_df = climate_df.drop(columns=["points", "time"])

    # Get the number of rows and columns
    num_rows, num_cols = climate_df.shape

    # Reshape the DataFrame to a 3D array (groups, 12, columns)
    reshaped_array = climate_df.to_numpy().reshape(-1, 12, num_cols)

    # Transpose and reshape to get the desired flattening effect
    result_array = reshaped_array.transpose(0, 2, 1).reshape(-1, 12 * num_cols)

    # Convert back to a DataFrame if needed
    result_df = pd.DataFrame(result_array)
    # Set the new column names for the dataframe (climate variables X months
    # of the hydrological year)
    result_df.columns = _generate_climate_variable_names(ds_climate)
    return result_df


def _process_altitude_data(
    ds_geopotential: xr.Dataset, df: pd.DataFrame
) -> pd.DataFrame:
    """Process altitude data for all points."""

    # 1. Create DataArrays for latitude and longitude
    lat_da = xr.DataArray(df["POINT_LAT"].values, dims="points")
    lon_da = xr.DataArray(df["POINT_LON"].values, dims="points")

    altitude_data_points = ds_geopotential.sel(
        latitude=lat_da,
        longitude=lon_da,
        method="nearest",
    )

    return altitude_data_points.to_dataframe()


def _combine_dataframes(
    df: pd.DataFrame, climate_df: pd.DataFrame, altitude_df: pd.DataFrame
) -> pd.DataFrame:
    """Combine DataFrames and add altitude data."""
    df = df.drop(columns=["range_date"]).reset_index(drop=True)
    climate_df = climate_df.reset_index(drop=True)
    altitude_df = altitude_df.drop(columns=["latitude", "longitude", "z"]).reset_index(
        drop=True
    )

    df = pd.concat([df, climate_df, altitude_df], axis=1)
    #df["ALTITUDE_CLIMATE"] = altitude_df.altitude_climate.values
    # rename column
    df.rename(columns={"altitude_climate": "ALTITUDE_CLIMATE"}, inplace=True)
    df.dropna(subset=["ALTITUDE_CLIMATE"], inplace=True)

    return df


def _calculate_elevation_difference(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate the difference between geopotential height and stake measurement elevation."""
    df["ELEVATION_DIFFERENCE"] = df["ALTITUDE_CLIMATE"] - df["POINT_ELEVATION"]
    return df
