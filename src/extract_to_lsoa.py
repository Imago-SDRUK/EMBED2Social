"""
Imago Project - extraction of Google Earth Engine collections
(such as Google Satellite Embeddings)
for the area and year of interest at the level of polygon geometries
(such as LSOAs in the UK)

# Maintainers:
# Vitaly Kryukov <Vitaly.Kryukov@newcastle.ac.uk>

Usage:
    python src/extract_to_lsoa.py [OPTIONS]

-p, --project TEXT
        Google Cloud project ID used for authentication and storage access
        (default: imago)

--mode [drive|cloud]
        Where to read input GeoTIFFs from and where to write outputs.
        Currently only "cloud" (Google Cloud Storage) is fully supported.
        (default: cloud)

--tiles PATH
        Path to the gridded (tiled) area of interest.
        GeoPackage recommended, but other vector formats are supported.
        WARNING: must contain a "tile_name" column.
        (default: data/uk_20km_grid.gpkg)

--input-areas PATH
        Vector dataset with small polygon areas (e.g. LSOAs) and tile
        membership encoded in attributes (see --tiles option)
        WARNING: must contain join key used for aggregation
        (default: data/uk_lsoa_tiles.gpkg)

--input-folder TEXT
        Google Cloud Storage bucket or folder containing source GeoTIFFs.
        Data is expected under a subfolder named by year.
        (default: embed2social-storage)

--output-folder TEXT
        Google Cloud Storage bucket or folder for aggregated outputs.
        NOTE: bucket must exist beforehand.
        (default: embed2social-storage)

--out-format [gpkg|parquet]
        Output format for aggregated zonal statistics.
        - parquet: statistics only
        - gpkg: statistics joined with --input-areas data
        (default: gpkg)

-y, --year INTEGER
        Year of data to process.
        Used to locate input GeoTIFFs under input-folder/year.
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
        Controls memory usage and parallelism.
        (default: 4)

--help
        Show this message and exit.


WARNING: this is available if gcloud 
credentials were created beforehand.
To create credentials:
`export GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json`

ADC Credentials should be mounted into the docker, eg:
~/.config/gcloud/application_name_credentials.json
Further information: https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment
ADC credentials should have write permissions, like `roles/storage.objectCreator` or `roles/storage.admin`
"""

# NOTE - assume Imago package is already installed in the docker through PIP and can be imported

import gcsfs
import os
import pandas as pd
import geopandas as gpd
from pathlib import Path
import time

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import storage

from typing import List, Optional

import click
import logging

from google.cloud import storage
import tempfile

# internal tools
import imago
from imago.io.lsoa_extraction import extract_lsoa_zonal_stats
from imago.utils.tiles import bng_tile_regex

from utils import setup_logger, logger

logger: logging.Logger # to make clear that 'logger' is global by design

def gcs_auth_init(project: str, verbose: bool = True):
    """Initialize Google Cloud Storage client using ADC"""

    try:
        credentials, detected_project = google.auth.default()

        client = storage.Client(
            project=project or detected_project,
            credentials=credentials
        )

        if verbose:
            logger.info("GCS authenticated using Application Default Credentials (ADC)")
            logger.info("=" * 80)

        return client

    except DefaultCredentialsError:
        raise RuntimeError(
            "No Google Cloud credentials found. "
            "Run 'gcloud auth application-default login' "
            "or mount credentials in Docker."
        )

def get_matching_tiffs(
    input_path: str,
    tiles_gdf: Optional[gpd.GeoDataFrame] = None,
    tile_name_col: str = 'tile_name',
    tile_names: Optional[List[str]] = None,
) -> List[str]:

    if tile_names is None:
        tile_names = tiles_gdf[tile_name_col].unique().tolist()
        logger.info(f"Extracted tile names: {len(tile_names)}")

    # set up GCS path which uses ADC credentials automatically
    if input_path.startswith("gs://"):
        fs = gcsfs.GCSFileSystem()  # uses ADC automatically
        all_files = fs.ls(input_path)

        tiff_files = [
            f for f in all_files
            if f.lower().endswith(".tif")
        ]

    # define rollback to local files just in case
    else:
        all_files = os.listdir(input_path)
        tiff_files = [
            os.path.join(input_path, f)
            for f in all_files
            if f.lower().endswith(".tif")
        ]

    matching_tiffs = [
        f for f in tiff_files
        if any(Path(f).name.startswith(tile) for tile in tile_names)
    ]

    logger.info(f"Matching GeoTIFFs: {len(matching_tiffs)}")
    for f in matching_tiffs:
        logger.debug(f)

    return matching_tiffs

# CHECK IF FILENAMES PATTERN WORK - a separate helper function
"""
# Dummy full paths (simulating mounted files)
tiles_dummy = [
    "{input_folder}/2024/TQ06-2024.tif",
    "{input_folder}/2024/TQ08-2024.tif",
    "{input_folder}/2024/TQ26-2024.tif",
    "{input_folder}/2024/TQ28-2024.tif",
    "{input_folder}/2024/TQ46-2024.tif",
    "{input_folder}/2024/TQ48-2024.tif"
]

# Compile the default regex from the function for TIFF files
pat = bng_tile_regex('tif')

# Test each path
for full_path in tiles_dummy:
    # Extract only the filename from the full path
    fname = Path(full_path).name

    # Apply the regex to the filename
    match = pat.search(fname)
    if match:
        print(f"{full_path} -> tile ID: {match.group('tile')}")
    else:
        print(f"{full_path} -> NO MATCH")
"""

## separate helper - checking parquet stats
"""
# HELPER
if verbose:
    out_lsoa_dd = dd.read_parquet(out_lsoa_parquet)
    print(out_lsoa_dd.head())

    # Compute min and max for all columns
    mins = out_lsoa_dd.min().compute()
    maxs = out_lsoa_dd.max().compute()

    logger.debug("Minimum values for all columns:")
    logger.debug(mins)
    logger.debug("Maximum values for all columns:")
    logger.debug(maxs)
"""

"""
def parquet_to_gpkg(parquet_path, lsoas_path, output_gpkg_path, join_key="data_zone_code"):
    '''
    Convert aggregated parquet stats into a GeoPackage by joining
    them with LSOA geometries.
    '''
    global logger

    logger.info("Converting parquet stats to GeoPackage...")

    # Load geometries
    lsoas_gdf = gpd.read_file(lsoas_path)

    # Load stats (Dask -> pandas)
    out_lsoa_dd = dd.read_parquet(parquet_path)
    out_lsoa_pd = out_lsoa_dd.compute()

    # Join
    lsoas_merged = lsoas_gdf.merge(
        out_lsoa_pd,
        on=join_key,
        how="left"
    )

    # Save
    lsoas_merged.to_file(output_gpkg_path, driver="GPKG")

    logger.info(f"Output saved to geopackage: {output_gpkg_path}")
"""

def df_to_gpkg(df, input_areas, output_gpkg_path, gcs_client, join_key="data_zone_code"):
    logger.info("Converting DataFrame stats to GeoPackage...")

    # CHECK 1: stats dataframe
    if df is None or df.empty:
        raise ValueError("Zonal stats DataFrame is empty — nothing to write")
    if join_key not in df.columns:
        raise KeyError(f"Join key '{join_key}' not found in stats DataFrame")
    logger.debug(f"Stats DF rows: {len(df)}")

    #load geometries
    input_areas_gdf = gpd.read_file(input_areas)
    if join_key not in input_areas_gdf.columns:
        raise KeyError(f"Join key '{join_key}' not found in input areas")
    logger.debug(f"Input areas rows: {len(input_areas_gdf)}")

    # MERGE
    areas_merged = input_areas_gdf.merge(
        df,
        on=join_key,
        how="left",
        indicator=True
    )

    # CHECKS ON MERGE and EMPTY GEOMETRY
    merge_counts = areas_merged["_merge"].value_counts().to_dict()
    logger.debug(f"Merge result: {merge_counts}")
    if merge_counts.get("both", 0) == 0:
        raise ValueError(
            "Merge produced zero matched rows — check join_key values"
        )
    areas_merged.drop(columns="_merge", inplace=True)

    if areas_merged.geometry.isna().any():
        raise ValueError("Merged GeoDataFrame contains null geometries")

    # Write to destination
    # Unlike Parquet, which works fine with /vsigs/ paths, pyogrio/GeoPackage
    # relies on SQLite under the hood, and SQLite cannot create
    # a database directly in a cloud bucket.
    # the block below attempts to write into a temp file and then upload this blob to GCS
    if str(output_gpkg_path).startswith("gs://"):
        bucket_name, blob_name = output_gpkg_path[5:].split("/", 1)

        tmp = tempfile.NamedTemporaryFile(
            suffix=".gpkg",
            delete=False
        )
        tmp_path = tmp.name
        tmp.close()  # critical for SQLite/GDAL

        try:
            areas_merged.to_file(tmp_path, driver="GPKG")

            # CHECK on file size
            size = os.path.getsize(tmp_path)
            logger.debug(f"Temporary GPKG size: {size / 1024:.1f} KB")
            if size == 0:
                raise RuntimeError("GeoPackage written but file size is 0 bytes")

            # UPLOAD
            bucket = gcs_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(
                tmp_path,
                content_type="application/geopackage+sqlite3"
            )

            logger.info(f"Uploaded GeoPackage to GCS: {output_gpkg_path}")

        finally:
            os.remove(tmp_path)

    else:
        areas_merged.to_file(output_gpkg_path, driver="GPKG")
        logger.info(f"GeoPackage saved locally: {output_gpkg_path}")

# CHECKS
"""
n_tiles=len(tiles)
print(f"Number of tiles: {n_tiles}")
print(f"Matching tiffs: {len(matching_tiffs)}")
print("CPU cores:", os.cpu_count())
# Only 2 cores - only 2 parallelised tasks
"""

@click.command(context_settings=dict(show_default=True))
@click.option(
    "-p", "--project",
    default="imago",
    help="Google Earth Engine project ID"
)
@click.option(
    "--mode",
    type=click.Choice(["drive", "cloud"], case_sensitive=False),
    default="cloud",
    help="Where to read and write data (Google Drive or Cloud Storage)"
)
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
    "--input-folder",
    default="embed2social-storage",
    help="Input folder or bucket for source data"
)
@click.option(
    "--output-folder",
    default="embed2social-storage",
    help="Folder or bucket for output GeoTIFFs"
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
    project,
    mode,
    tiles,
    input_areas,
    input_folder,
    output_folder,
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
    # NOTE - there are two options:
    # global logger (implemented for the sake of clarity)
    # define logger in the each function as a separate parameter

    logger.info(f"Extracting {input_folder} to areas {tiles} for {year} year...")
    logger.info("=" * 80)

    start = time.time()

    # INPUT PATH and AUTHENTICATION
    gcs_path = f"gs://{input_folder}/{year}"
    drive_path = f"gdrive/MyDrive/embed_workshop/data/tif/{year}"
    if mode == "drive":
        # NOTE - DRIVE authorisation is not implemented
        input_path=drive_path
    if mode == "cloud":
        logger.info("Initializing GCS authentication...")
        gcs_client = gcs_auth_init(project=project, verbose=verbose)
        input_path=gcs_path
        logger.info(f"Input path is {input_path}")

    # Load grid and create paths
    grid = gpd.read_file(tiles)
    if "tile_name" not in grid.columns:
        raise ValueError("Input tiles must contain a 'tile_name' column")
    tile_name_col = 'tile_name' # NOTE - hardcoded

    # Define output paths
    # NOTE - GCS bucket should be created beforehand
    input_name = Path(input_areas).stem  # e.g., "lsoa_areas" from "data/lsoa_areas.gpkg"
    if mode == "cloud":
        out_lsoa_parquet = f"gs://{output_folder}/{input_name}_stats_{year}.parquet"
        out_lsoa_gpkg = f"gs://{output_folder}/{input_name}_stats_{year}.gpkg"
    # NOTE - Google Drive mode is not implemented

    # Get names of TIFFs that match the desired tile names
    matching_tiffs = get_matching_tiffs(
        input_path=input_path,
        #tiles_gdf=grid,
        #tile_name_col=tile_name_col,
        tile_names=["NZ26", "NZ28"]
    )

    # FIX FOR GCS RASTERIO 
    # Attach /vsigs/ to paths so rasterio can open them directly from GCS
    for i, path in enumerate(matching_tiffs):
        if path.startswith("gs://"):
            # gs://bucket/path/to/file.tif -> /vsigs/bucket/path/to/file.tif
            matching_tiffs[i] = "/vsigs/" + path[5:]  
        elif not os.path.exists(path):
            # If local path does not exist but bucket path is known
            # convert to /vsigs/ path assuming input_path is GCS bucket
            matching_tiffs[i] = "/vsigs/" + input_path[5:] + "/" + Path(path).name

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
            gcs_client=gcs_client,
            output_gpkg_path=out_lsoa_gpkg
        )
        logger.info(f"Output saved to geopackage: {out_lsoa_gpkg}")

    # TODO - JOIN SHOULD BE TO UNIQUE VALUES, NOT DATASET WITH TILE_NAMES (many features for the same LSOA if in multiple tiles)
    # TODO - to remove column with tile names

    end = time.time()
    logger.info(f'Total time: {end - start:.2f} seconds')

if __name__ == "__main__":
    main()


#INSTRUCTIONS
"""
# In Docker: install git
git clone download_gee (first part)
git clone imago
pip install ./imago (in imago)
run ./download_gee.py / extract2lsoa.py

# Two paths 
tile_path (input TIFFS)
storage-path (output geopackage)"""