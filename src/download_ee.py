"""
Imago Project - Batch export of Google Earth Engine collections
for the area and year of interest
(such as Google Satellite Embeddings)

# Maintainers:
# Vitaly Kryukov <Vitaly.Kryukov@newcastle.ac.uk>

Usage:
    python src/main.py [OPTIONS]

OPTIONS
    --collection TEXT        Earth Engine image collection to extract 
                             (default: GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL). 
                             Available public collections can be found in the Earth Engine Data Catalog:
                             https://developers.google.com/earth-engine/datasets
                             WARNING: Only annual collections currently supported 
                             (otherwise, one timestamp will be exported)

    --project TEXT           Google Earth Engine project ID (default: imago)

    --auth-mode TEXT         Earth Engine authentication mode (default: gcloud)

    --tiles PATH             Path to the gridded (tiled) area of interest. 
                             GeoPackage recommended, but other formats such as GeoJSON are supported.
                             WARNING: "tile_name" column must be included.

    --storage [drive|cloud]  Export destination - Google Drive or Google Cloud Space (default: drive).
                             Google Cloud Space is recommended, as Google Drive requires a folder created 
                             beforehand; files cannot be easily rewritten.

    --folder TEXT            Drive folder or Cloud Storage bucket (default: embed2social-storage)

    --year INTEGER           Year to extract embeddings for. 
                             The extracted tiles will be saved to subfolder 
                             with the name equal to year (default: 2024).

    --verbose                Enable verbose logging (WARNING: can produce very large logs)

    --cog                    Export as Cloud Optimized GeoTIFF (COG)

    --crs TEXT               Output coordinate reference system (CRS) for exports
                             (default: EPSG:27700).
                             Example: EPSG:4326, EPSG:3857.

    --res INTEGER            Output spatial resolution (pixel size) for exports
                             (default: 10).
                             WARNING: for geographic CRS provided in degrees,
                             for projected - in meters.

    --scale                  Multiply output data by 32767 and round to store as Int16.
                             Useful for heavyweight datasets with Float data type
                             (has been used to scale Google Satellite Embeddings). 
                             Include this flag if scaling is needed; omit to keep float values.

USAGE EXAMPLES
    # Run with default settings
    python src/main.py

    # Example of export in non-commercial project (for testing)
    python src/main.py --year 2024 --project imago --scale --storage drive --folder cli-test --tiles data/uk_1km_grid_sample1.gpkg

    # Test with ESA world cover ("ESA/WorldCover/v100" collection)
    python src/main.py --year 2020 --collection ESA/WorldCover/v100 --project imago --storage drive --folder cli-test --tiles data/uk_1km_grid_sample.gpkg

    # Test with ESA/WorldCereal/2021/MARKERS/v100 colelction
    python src/main.py --year 2021 --collection ESA/WorldCereal/2021/MARKERS/v100 --project imago --storage drive --folder cli-test --tiles data/uk_1km_grid_sample.gpkg

    # Help
    python src/main.py --help
"""

import ee
import json
import geopandas
from typing import Tuple
import time
from datetime import datetime, timezone
import os

import click # command line
import logging

logger: logging.Logger # to make clear that 'logger' is global by design

def setup_logger(verbose: bool = False, log_dir: str = "logs"):
    """
    Configure the logger to write all output to a file.
    
    Args:
        verbose: If True, set logging level to DEBUG; else INFO.
        log_dir: Directory to store log files.
    
    Returns:
        Configured logger object.
    """

    global logger 

    os.makedirs(log_dir, exist_ok=True)
    filename = f"logfile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logfile = os.path.join(log_dir, filename)

    logger = logging.getLogger("imago")
    logger.handlers.clear()
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    # logger.propagate = False # NOTE - try if debug is not printed

    # File handler
    file_handler = logging.FileHandler(logfile)
    file_handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info(f"Logging started -> {logfile}")
    logger.info("=" * 80)

def auth_init(project: str ='imago', auth_mode: str ='gcloud', verbose: bool = False):
    """Start session on GEE"""
    try:
        ee.Initialize(project=project)
        logger.info("Project initialised using existing credentials")
    except ee.EEException:
        logger.info("No valid credentials, authenticating...")
        ee.Authenticate(auth_mode=auth_mode)
        ee.Initialize(project=project)
        logger.info("Authenticated and initialized")

    logger.info(ee.String('Hello from the Earth Engine servers!').getInfo())
    logger.info("=" * 80)

def build_cell_year(
    cell: "geopandas.GeoDataFrame",
    year: int,
    collection: str = 'GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL',
    verbose: bool = False
) -> Tuple[ee.Image, ee.Geometry, int]:
    """
    Build a GEE Image with embeddings from year `year` in a grid cell `cell`.

    Args:
        collection: path to the GEE collection.
        cell: GeoPandas GeoDataFrame containing exactly one row representing a grid cell geometry.
        year: year for which embeddings are required
        verbose: print detailed logs. WARNING: this might bloat up logs
        when running batch export with many tasks.
            Default is False
    Returns:
        image: ee.Image containing embeddings clipped to the cell geometry
        ee_geometry: ee.Geometry of the grid cell (WGS84)
        count: Number of embedding tiles intersecting the cell for the given year.
    """

    # This handles transformation from geodataframe to GEE geometry
    # without JSON indexing and intermediate FeatureCollection wrapping
    # Initial geometries can have multipolygons/empty geometry/row filtering might break
    geom = cell.to_crs(4326).geometry.iloc[0]
    if geom is None or geom.is_empty:
        raise ValueError("Empty geometry")
    ee_geometry = ee.Geometry(geom.__geo_interface__)

    # load the satellite embedding collection
    collection = ee.ImageCollection(collection)
    # filter by date and region
    start_date = f'{year}-01-01'
    end_date = f'{year+1}-01-01'
    # get all images that overlap the geometry
    filtered = collection.filterDate(start_date, end_date).filterBounds(ee_geometry)

    count_eenumber = filtered.size()
    count=count_eenumber.getInfo() # now it's a Python int

    if verbose:
        #  tile metadata
        tiles_list = filtered.toList(count)
        logger.info(f"Found {count} images intersecting this tile/region")
        if verbose:
            for i in range(count):
                img = ee.Image(tiles_list.get(i))
                info = img.getInfo()
                logger.debug("-" * 30)
                logger.debug(f"Tile {i+1}")
                logger.debug(f"ID: {info['id']}")
                first_band = info['bands'][0]
                logger.debug(f"CRS: {first_band['crs']}")
                logger.debug(f"Resolution: {first_band['crs_transform'][0]}")
                # NOTE: - it's possible to move statements above to the 'TRACE' level instead of 'DEBUG'
                # as calls to `img.getinfo` are costly and take time (task are not queueing up while `img.getinfo` runs)
                # However, left in 'DEBUG' for the sake of clarity
    
    # create mask for the geometry
    roi_mask = ee.Image(1).clip(ee_geometry)
    # apply mask to all images
    masked = filtered.map(lambda img: img.updateMask(roi_mask))
    # handle single vs multiple tiles
    if count == 1:
        verbose and logger.debug("Single tile covers this region")
        image = masked.first()
    else:
        verbose and logger.debug(f" Mosaicking {count} images which cover this tile/region....")
        logger.debug("-" * 60)
        image = masked.mosaic() 
        # which cover the same extent (as mosaic() just returns values from one image)
    # clip to geometry
    image = image.clip(ee_geometry)
    
    return image, ee_geometry, count

def probe_image_properties(image: ee.Image, geometry: ee.Geometry = None):
    """
    Helper to extract comprehensive information 
    about an ee.Image based on 10 m spatial resolution.
    It is not recommended to use with many tasks, as it overloads the log.
    
    Args:
        image: Earth Engine image to probe
        geometry: Optional geometry to sample from
    """
    logger.debug("=" * 80)
    logger.debug("IMAGE PROPERTIES")
    logger.debug("=" * 80)
    
    # Band information
    bands = image.bandNames().getInfo()
    logger.debug(f"Bands ({len(bands)} total):")
    logger.debug(f"  {bands[:5]}...{bands[-3:]}")  # Show first 5 and last 3
    
    # Get projection and scale info from first band
    projection = image.select([bands[0]]).projection()
    logger.debug(f"Projection:")
    logger.debug(f"  CRS: {projection.crs().getInfo()}")
    logger.debug(f"  Native scale: {projection.nominalScale().getInfo()} meters")
    
    # Get pixel type information
    band_types = image.bandTypes().getInfo()
    logger.debug(f"Data Types:")
    for band, dtype in list(band_types.items())[:3]:  # Show first 3
        logger.debug(f"  {band}: {dtype['precision']}")
    
    # Get image bounds
    if geometry:
        bounds = geometry.bounds().getInfo()
    else:
        # Try to get image footprint
        try:
            bounds = image.geometry().bounds().getInfo()
        except:
            bounds = None
            
    if bounds:
        logger.debug(f"Bounds:")
        coords = bounds['coordinates'][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        logger.debug(f" Longitude: [{min(lons):.4f}, {max(lons):.4f}]")
        logger.debug(f" Latitude: [{min(lats):.4f}, {max(lats):.4f}]")
        
        # Approximate size
        lon_range = max(lons) - min(lons)
        lat_range = max(lats) - min(lats)
        width_km = lon_range * 111.32
        height_km = lat_range * 110.54
        logger.debug(f"Approximate dimensions:")
        logger.debug(f"  Width: ~{width_km:.1f} km")
        logger.debug(f"  Height: ~{height_km:.1f} km")
        
        # Estimate pixel dimensions at 10m resolution
        pixels_width = int(width_km * 1000 / 10)
        pixels_height = int(height_km * 1000 / 10)
        logger.debug(f" Pixels (at 10m): ~{pixels_width} x {pixels_height}")
        logger.debug(f" Total pixels: ~{pixels_width * pixels_height:,}")
        
        # estimate file size
        n_bands = len(bands)
        size_bytes = pixels_width * pixels_height * n_bands * 4
        size_mb = size_bytes / (1024**2)
        size_gb = size_bytes / (1024**3)
        logger.debug(f"Estimated uncompressed size:")
        if size_gb > 1:
            logger.debug(f"  ~{size_gb:.2f} GB")
        else:
            logger.debug(f"  ~{size_mb:.1f} MB")
    
    logger.debug("=" * 80)
    return None

def export_cloud(
    image: ee.Image,
    geometry: ee.Geometry,
    description: str,
    prefix: str,
    folder: str = 'EarthEngine',
    crs: str = 'EPSG:4326',
    res: int = 10,
    cog: bool  = False
) -> ee.batch.Task:
    """
    Builds and launches batch export tasks to Google Cloud Storage as GeoTIFF 
    (Cloud Optimised GeoTIFF as optional). The client only initiates the task.
    Once started, task runs entirely on GEE servers, even if the client disconnects.
    This is the RECOMMENDED method for most cases.
    
    Args:
        image (ee.Image): Earth Engine image
        geometry (ee.Geometry): Region to export
        description (str): Export task name
        folder (str): Google Cloud Storage bucket name. 
            The location inside the bucket is defined by `prefix`.
        prefix (str): File name prefix 
            (can include pseudo-folder path inside the bucket)
        crs (str): Output coordinate system reference system.
            Default is 'EPSG:4326'
        res (int): Output spatial resolution (pixel size).
            Default is 10.         
        cog (bool): whether to save to COG. Default is False
    Returns:
        ee.batch.Task: Earth Engine batch export task object
    """
    task = ee.batch.Export.image.toCloudStorage(
        image=image,
        description=description,
        bucket=folder,
        fileNamePrefix=prefix,
        region=geometry,
        scale=res,
        crs=crs,
        maxPixels=1e13,
        fileFormat='GeoTIFF',
        formatOptions={
            'cloudOptimized': cog
        }
    )
    
    task.start()
    logger.debug(f"Export started: {description}")
    logger.debug(f"Monitor at: https://code.earthengine.google.com/tasks")
    logger.info(f"Will be saved to: /{folder}/{prefix}.tif")
    logger.info(f"-" * 60)
    
    return task

def export_drive(
    image: ee.Image,
    geometry: ee.Geometry,
    description: str,
    prefix: str,
    folder: str = 'EarthEngine',
    crs: str = 'EPSG:4326',
    res: int = 10,
    cog: bool = False
) -> ee.batch.Task:
    """
    Builds and launches batch export tasks to Google Drive as GeoTIFF 
    (Cloud Optimised GeoTIFF as optional). The client only initiates the task.
    Once started, task runs entirely on GEE servers, even if the client disconnects.

    Args:
        image (ee.Image): Earth Engine image
        geometry (ee.Geometry): Region to export
        description (str): Export task name
        folder (str): Google Drive folder (main path)
        prefix (str): File name prefix 
            (can include nested folder path inside the main folder)
        crs (str): Output coordinate system reference system.
            Default is 'EPSG:4326'
        res (int): Output spatial resolution (pixel size).
            Default is 10.   
        cog (bool): whether to save to COG. Default is False
    Returns:
        ee.batch.Task: Earth Engine batch export task object
    """
    task = ee.batch.Export.image.toDrive(
        image=image,
        description=description,
        folder=folder,
        fileNamePrefix=prefix,
        region=geometry,
        scale=res,
        crs=crs,
        maxPixels=1e13,
        fileFormat='GeoTIFF',
        formatOptions={
            'cloudOptimized': cog
        }
    )

    task.start()
    logger.debug(f"Export started: {description}")
    logger.debug(f"Monitor at: https://code.earthengine.google.com/tasks")
    logger.info(f"Will be saved to: /{folder}/{prefix}.tif")
    logger.info(f"-" * 60)
    
    return task

def wait_for_all_tasks(tasks, poll_interval=60):
    """
    Wait until all Earth Engine tasks complete and record total runtime.
    Uses batch polling via ee.data.getTaskStatus (1 API call per poll).

    Args:
        tasks: list of ee.batch.Task objects
        poll_interval: seconds to wait between status checks
    
    Returns:
        total_seconds: float, wall-clock seconds for all tasks
        all_statuses: list of full task metadata dicts
    """
    task_ids = [task.id for task in tasks]
    logger.info(f"Waiting for {len(task_ids)} tasks to complete...")

    start_time = time.time()

    while True:
        # single API calls for all tasks
        statuses = ee.data.getTaskStatus(task_ids)
        states = [s.get("state", "UNKNOWN") for s in statuses]

        # check terminal states
        if all(state in ("COMPLETED", "FAILED", "CANCELLED") for state in states):
            break

        time.sleep(poll_interval)

    total_seconds = time.time() - start_time
    logger.info("=" * 80)

    # normalise timtestamps
    for status in statuses:
        for key in ("creation_timestamp_ms", "start_timestamp_ms", "update_timestamp_ms"):
            if key in status:
                status[key] = datetime.fromtimestamp(
                    status[key] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S")
    
    return total_seconds, statuses, task_ids

def get_operations_metadata(task_ids, project, verbose=False):
    """
    Given a list of task IDs, fetch and print operation metadata for each.
    """
    all_metadata = []
    
    for task_id in task_ids:
        operation = ee.data.getOperation(f'projects/{project}/operations/{task_id}')
        # Convert timestamps to human-readable format
        for key in ['creation_timestamp_ms', 'start_timestamp_ms', 'update_timestamp_ms']:
            if key in operation:
                operation[key] = datetime.fromtimestamp(operation[key]/1000).strftime('%Y-%m-%d %H:%M:%S')
        all_metadata.append(operation)

    if verbose:
        logger.debug("--- All operation metadata ---")
        logger.debug(json.dumps(all_metadata, indent=4))
    return all_metadata

@click.command(context_settings=dict(show_default=True))
@click.option(
    "--collection",
    default="GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL",
    help="Earth Engine image collection to extract"
)
@click.option(
    "-p", "--project",
    default="imago",
    help="Google Earth Engine project ID"
)
@click.option(
    "--auth-mode",
    default="gcloud",
    help="Earth Engine authentication mode"
)
@click.option(
    "--tiles",
    default="data/uk_1km_grid_sample1.gpkg",
    type=click.Path(exists=True),
    help="Path to the gridded area of interest (tiles) to download the Embeddings"
)
@click.option(
    "--storage",
    type=click.Choice(["drive", "cloud"], case_sensitive=False),
    default="drive",
    help="Export destination - either Google Drive or Google Cloud Storage"
)
@click.option(
    "--folder",
    default="embed2social-storage",
    help="Drive folder or Cloud Storage bucket"
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
    help="Enable verbose logging (WARNING: can produce very large logs)"
)
@click.option(
    "--cog",
    is_flag=True,
    help="Export as Cloud Optimized GeoTIFF (COG)"
)
@click.option(
    "--crs",
    default="EPSG:27700",
    help="Coordinate Reference System (CRS) of the output"
)
@click.option(
    "--res",
    default=10,
    type=int,
    help="Spatial resolution of output"
)
@click.option(
    "--scale",
    is_flag=True,
    help="Scale to Int16, multiplying by 32767. Useful for heavyweight datasets with Float data type."
)

def main(
    collection,
    tiles,
    project,
    auth_mode,
    storage,
    folder,
    year,
    verbose,
    cog,
    crs,
    res,
    scale
):
    """
    Batch export annual satellite imagery 
    for a grid of spatial tiles and area of 
    interest using Google Earth Engine 
    (for example, Google Satellite Embeddings).
    """

    global logger 
    setup_logger(verbose=verbose, log_dir="logs")
    # NOTE - there are two options:
    # global logger (implemented for the sake of clarity)
    # define logger in the each function as a separate parameter

    logger.info(f"Extracting {collection} collection for {year} year...")
    logger.info("=" * 80)
    tasks = [] 
    # Authenticate
    auth_init(project=project, auth_mode=auth_mode, verbose=verbose)

    # Load grid
    grid = geopandas.read_file(tiles)
    if "tile_name" not in grid.columns:
        raise ValueError("Input tiles must contain a 'tile_name' column")
    
    for _, row in grid.iterrows():
        cell = row["tile_name"]
        cell_gdf = grid.loc[[row.name]]
  
        description = f"{cell}-{year}"
        prefix = f"{year}/{cell}-{year}"
        logger.info(f"Tile/region: {description}...")

        cell_gee_image, cell_geometry, count = build_cell_year(
            cell_gdf,
            year,
            collection=collection,
            verbose=verbose,
        )

        # scale data to int16 (if dataset is Embeddings)
        if scale:
            logger.debug("Scaling image: multiply by 32767 and round (scale to Int16)")
            cell_gee_image = (
                cell_gee_image
                .clamp(-1.0, 1.0)
                .multiply(32767)
                .round()
                .toInt16()
            )

        # choose between cloud and drive for export destination
        if storage == "cloud":
            logger.debug("Exporting to Google Cloud Storage...")
            task = export_cloud(
                image=cell_gee_image,
                geometry=cell_geometry,
                description=description,
                prefix=prefix,
                folder=folder,
                crs=crs,
                res=res,
                cog=cog
            )

        else:
            logger.debug("Exporting to Google Drive...")
            task = export_drive(
                image=cell_gee_image,
                geometry=cell_geometry,
                description=description,
                prefix=prefix,
                folder=folder,
                crs=crs,
                res=res,
                cog=cog
            )

        if verbose:
            probe_image_properties(cell_gee_image)

        tasks.append(task)

    # wait for all tasks
    total_seconds, all_statuses, task_ids = wait_for_all_tasks(
        tasks
    )

    total_hours = total_seconds / 3600
    logger.info("=" * 80)
    logger.info(f"Pipeline runtime: {total_seconds:.2f} seconds")
    logger.info(f"  -> {total_hours:.2f} hours")

    # fetch operation metadata and compute total EECU
    all_ops_metadata = get_operations_metadata(task_ids, project=project, verbose=verbose)
    total_eecu = 0.0

    for op in all_ops_metadata:
        eecu = op.get("metadata", {}).get("batchEecuUsageSeconds", 0)
        total_eecu += eecu

    logger.info("=" * 80)
    logger.info(f"Total billable EECU time: {total_eecu:.2f} seconds")
    logger.info(f"  ↳ {total_eecu / 3600:.2f} hours")
    logger.info("=" * 80)

if __name__ == "__main__":
    main()