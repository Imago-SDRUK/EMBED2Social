"""
Imago Project - extraction of Google Earth Engine collections
(such as Google Satellite Embeddings)
for the area and year of interest at the level of polygon geometries
(such as LSOAs in the UK)

# Maintainers:
# Vitaly Kryukov <Vitaly.Kryukov@newcastle.ac.uk>

Usage:
    python src/extract_to_lsoa.py [OPTIONS]

--tiles PATH
        Path to the gridded (tiled) area of interest.
        GeoPackage recommended, but other vector formats are supported.
        WARNING: MUST contain a "tile_name" column with the names of reference tiles.
        (default: data/uk_20km_grid.gpkg)

--input-areas PATH
        Vector dataset with small polygon areas (e.g. LSOAs) and tile
        membership encoded in attributes (see --tiles option)
        WARNING: must contain join key used for aggregation
        (default: data/uk_lsoa_tiles.gpkg)

--storage-path TEXT
        Folder containing source GeoTIFFs.
        Data is expected under a subfolder named by year.
        (default: data/embed2social-storage)

--output-path TEXT
        Folder to store outputs.
        (default: data/embed2social-storage/output)

--out-format [gpkg|parquet]
        Output format for aggregated zonal statistics.
        - parquet: statistics only
        - gpkg: statistics joined with --input-areas data
        (default: gpkg)

-y, --year INTEGER
        Year of data to process.
        (default: 2024)

-v, --verbose
        Enable verbose logging.
        WARNING: can produce very large logs.

--scaling-factor FLOAT
        Scaling factor applied to pixel values before aggregation.
        Useful for integer-encoded embeddings (e.g. Google Satellite Embeddings).
        (default: 32767)

--npartitions INTEGER
        Number of Dask partitions used for parallel processing.
        (default: 4)

--help
        Show this message and exit.

"""

# NOTE - assume Imago package is already installed in the docker through PIP and can be imported

import os
import geopandas as gpd
from pathlib import Path
import time

from typing import List, Optional

import click
import logging

# internal tools
import imago
from imago.io.lsoa_extraction import extract_lsoa_zonal_stats
from imago.utils.tiles import bng_tile_regex

from utils import setup_logger, prints_to_logger, logger

logger: logging.Logger # to make clear that 'logger' is global by design

def get_matching_tiffs(
    input_path: str,
    tiles_gdf: Optional[gpd.GeoDataFrame] = None,
    tile_name_col: str = 'tile_name',
    tile_names: Optional[List[str]] = None,
) -> List[str]:

    if tile_names is None:
        tile_names = tiles_gdf[tile_name_col].unique().tolist()
        logger.info(f"Extracted tile names: {len(tile_names)}")

    # local path
    all_files = os.listdir(input_path)
    tiff_files = [
        os.path.join(input_path, f)
        for f in all_files
        if f.lower().endswith(".tif")
    ]

    matching_tiffs = [
        f for f in tiff_files
        if any(tile in Path(f).stem for tile in tile_names)
    ]

    logger.info(f"Matching GeoTIFFs: {len(matching_tiffs)}")
    for f in matching_tiffs:
        logger.debug(f)

    return matching_tiffs

def df_to_gpkg(df, input_areas, output_gpkg_path, tile_name_col="tile_name", join_key="data_zone_code"):
    """
    Convert a pandas dataframe of zonal statistics into a geopackage
    aggregated by polygon areas (eg, LSOAs).

    Parameters
    df: pandas.DataFrame
        dataframe containing zonal statistics for each polygon-tile combination.
        Must include the column specified in `join_key`.
    input_areas: str or Path
        Path to a vector dataset (Geopackage recommended) containing the polygon geometries
        to aggregate over. Must include the column specified in `join_key`.
    output_gpkg_path: str or Path
        Path to save the output GeoPackage.
    tile_name_col: str, default "tile_name_col"
        Tile column in `input_areas` to drop.
    join_key: str, default "data_zone_code"
        Column name used to join `df` with `input_areas` and to aggregate
        statistics.
    """
    logger.info("Converting Dataframe stats to Geopackage...")

    # CHECK 1: stats dataframe
    if df is None or df.empty:
        raise ValueError("Zonal stats DataFrame is empty — nothing to write")
    if join_key not in df.columns:
        raise KeyError(f"Join key '{join_key}' not found in stats DataFrame")
    logger.debug(f"Stats DF rows: {len(df)}")

    # Load geometries
    gdf = gpd.read_file(input_areas)
    if join_key not in gdf.columns:
        raise KeyError(f"Join key '{join_key}' not found in input areas")
    logger.debug(f"Input areas rows: {len(gdf)}")
    
    # Drop tile column and duplicates - we don't need them
    drop_cols = [col for col in [tile_name_col] if col in gdf.columns]
    if drop_cols:
        logger.debug(f"Dropping columns: {drop_cols}")
        gdf = gdf.drop(columns=drop_cols)

    # Keep only one row per unique join_key
    gdf_unique = gdf.drop_duplicates(subset=join_key)
    logger.debug(f"Unique geometries: {len(gdf_unique)} rows")
    # Merge stats
    gdf_merged = gdf_unique.merge(df, on=join_key, how="left", indicator=True)

    # CHECK merge
    merge_counts = gdf_merged["_merge"].value_counts().to_dict()
    logger.debug(f"Merge result: {merge_counts}")
    if merge_counts.get("both", 0) == 0:
        raise ValueError("Merge produced zero matched rows — check join_key values")
    gdf_merged.drop(columns="_merge", inplace=True)
    # Check for empty geometry
    if gdf_merged.geometry.isna().any():
        raise ValueError("Merged GeoDataFrame contains null geometries")

    # Save
    os.makedirs(os.path.dirname(output_gpkg_path), exist_ok=True)
    gdf_merged.to_file(output_gpkg_path, driver="GPKG")

@click.command(context_settings=dict(show_default=True))
@click.option(
    "--tiles",
    default="data/uk_20km_grid.gpkg",
    type=click.Path(exists=True),
    help="Path to the gridded area of interest (tiles) to download the embeddings"
)
@click.option(
    "--input-areas",
    default="data/uk_lsoa_tiles.gpkg",
    type=click.Path(exists=True),
    help="Dataset with the small polygon areas with tile names in the attributes"
)
@click.option(
    "--storage-path",
    default="data/embed2social-storage",
    help="Input path to storage for source data"
)
@click.option(
    "--output-path",
    default="data/embed2social-storage/output",
    help="Path for outputs"
)
@click.option(
    "--out-format",
    type=click.Choice(["gpkg", "parquet"], case_sensitive=False),
    default="gpkg",
    help="Output format (Parquet or GeoPackage)"
)
@click.option(
    "-y", "--year",
    default=2024,
    type=int,
    help="Year to extract dataset for"
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    help="Enable verbose logging (can produce very large logs)"
)
@click.option(
    "--scaling-factor",
    default=32767,
    type=float,
    help="Scaling factor applied to the pixel values"
)
@click.option(
    "--npartitions",
    default=4,
    type=int,
    help="Number of partitions to split processing tasks (parallelization)"
)

def main(
    tiles,
    input_areas,
    storage_path,
    output_path,
    out_format,
    year,
    verbose,
    scaling_factor,
    npartitions
):
    """
    Extract satellite collections in GeoTIFF format
    and aggregate them at the level of polygonal features

    In Imago Project:
    Extract Google Satellite Embeddings 
    and aggregate them at LSOA level.
    """

    global logger 
    setup_logger(verbose=verbose, log_dir="logs")
    # NOTE - there could two options:
    # global logger (implemented for the sake of clarity)
    # define logger in the each function as a separate parameter

    # Redirect all prints to the logger
    prints_to_logger("imago")
    logger.info(f"Extracting from {storage_path} to areas {tiles} for {year} year...")
    logger.info("=" * 80)

    start = time.time()

    # INPUT PATH
    local_path = f"{storage_path}/{year}"
    input_path=local_path
    logger.info(f"Input path is {input_path}")

    # Load grid and create paths
    grid = gpd.read_file(tiles)
    if "tile_name" not in grid.columns:
        raise ValueError("Input tiles must contain a 'tile_name' column")
    tile_name_col = 'tile_name' # NOTE - hardcoded

    # Define output paths
    input_name = Path(input_areas).stem  # e.g., "lsoa_areas" from "data/lsoa_areas.gpkg"
    
    out_lsoa_parquet =f"{output_path}/{input_name}_stats_{year}.parquet"
    out_lsoa_gpkg = f"{output_path}/{input_name}_stats_{year}.gpkg"

    # Get names of TIFFs that match the desired tile names
    matching_tiffs = get_matching_tiffs(
        input_path=input_path,
        tiles_gdf=grid,
        tile_name_col=tile_name_col
        # tile_names=["NZ26", "NZ28"] # NOTE - Debug for a small area
    )

    """
    logger.info(f"Matching tiffs: {matching_tiffs}")
    logger.info(f"Input areas: {input_areas}")
    logger.info(f"Out lsoa parquet: {out_lsoa_parquet}")"""

    # Extract stats (IMAGO module)
    result = extract_lsoa_zonal_stats(
        tiles_path=matching_tiffs,
        lsoa_path=input_areas, # gpkg with LSOAs and their tile names
        tile_name_col=tile_name_col,
        npartitions=npartitions,
        scaling_factor=scaling_factor,
        output_path=out_lsoa_parquet if out_format == "parquet" else None
    )
        
    if out_format == "parquet":
        logger.info(f"Output saved to parquet: {out_lsoa_parquet}")
    elif out_format == "gpkg":
        df_to_gpkg(
            df=result,
            input_areas=input_areas,
            tile_name_col=tile_name_col,
            output_gpkg_path=out_lsoa_gpkg
        )
        logger.info(f"Output saved to geopackage: {out_lsoa_gpkg}")

    # TODO - JOIN SHOULD BE TO UNIQUE VALUES, NOT DATASET WITH TILE_NAMES (many features for the same LSOA if in multiple tiles)
    # TODO - to remove column with tile names

    end = time.time()
    logger.info(f'Total time: {end - start:.2f} seconds')

if __name__ == "__main__":
    main()