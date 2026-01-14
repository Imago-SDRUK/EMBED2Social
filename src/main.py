# Imago Project
# Maintainers:
# Vitaly Kryukov <Vitaly.Kryukov@newcastle.ac.uk>
# Daniel Arribas-Bel <darribas@liverpool.ac.uk>

# This extracts annual AlpaEarth Foundation Embeddings 
# as a batch from the area and year of interest

# TO RUN:
# python main.py
# `nohup python main.py > logs/output_2021.log 2>&1 &` (to ignore disconnections, sleep, locks etc)
# pip install earthengine-api

import ee
import json
import geopandas
from typing import Optional, List, Tuple
import time
from datetime import datetime, timezone

def auth_init(project: str ='embed2social', auth_mode: str ='localhost'):
    """Start session on GEE"""
    try:
        ee.Initialize(project=project)
        print("Project initialised without credentials")
    except ee.EEException:
        print("No valid credentials, authenticating...")
        ee.Authenticate(auth_mode=auth_mode)
        ee.Initialize(project=project)
        print("Authenticated and initialized")
    print(ee.String('Hello from the Earth Engine servers!').getInfo())
    print("-" * 40)

def build_cell_year(
    cell: "gpd.GeoDataFrame",
    year: int,
    verbose: bool = False
) -> Tuple[ee.Image, ee.Geometry, int]:
    """
    Build a GEE Image with embeddings from year `year` in a grid cell `cell`.

    Args:
        cell: GeoPandas GeoDataFrame containing exactly one row representing a grid cell geometry.
        year: year for which embeddings are required
    Returns:
        image: ee.Image containing embeddings clipped to the cell geometry
        geometry: ee.Geometry of the grid cell (WGS84)
        count: Number of embedding tiles intersecting the cell for the given year.
    """
    geometry = ee.Geometry(
        json.loads(
            cell.to_crs('EPSG:4326').geometry.to_json()
        )['features'][0]['geometry']
    )
    # load the satellite embedding collection
    collection = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
    # filter by date and region
    start_date = f'{year}-01-01'
    end_date = f'{year+1}-01-01'
    # get all images that overlap the geometry
    filtered = collection.filterDate(start_date, end_date).filterBounds(geometry)
    count = filtered.size().getInfo()
    if count == 0:
        raise ValueError(f"No images found for year {year} in specified region")

    # tile metadata
    tiles_list = filtered.toList(count)
    print(f"Found {count} tile(s) intersecting this region")
    if verbose:
        for i in range(count):
            img = ee.Image(tiles_list.get(i))
            info = img.getInfo()
            print("-" * 40)
            print(f"Tile {i+1}")
            print(f"ID: {info['id']}")
            first_band = info['bands'][0]
            print(f"CRS: {first_band['crs']}")
            print(f"Resolution : {first_band['crs_transform'][0]}")
    
    # create mask for the geometry
    roi_mask = ee.Image(1).clip(geometry)
    # apply mask to all images
    masked = filtered.map(lambda img: img.updateMask(roi_mask))
    # handle single vs multiple tiles
    if count == 1:
        print(f" Single tile covers this region")
        image = masked.first()
    else:
        print(f" {count} tiles cover this region. Mosaicking...")
        image = masked.mosaic()
    # clip to geometry
    image = image.clip(geometry)
    
    return image, geometry, count

def probe_image_properties(image: ee.Image, geometry: ee.Geometry = None):
    """
    Helper to extract comprehensive information 
    about an ee.Image based on 10 m spatial resolution.
    It is not recommended to use with many tasks, as it overloads the log.
    
    Args:
        image: Earth Engine image to probe
        geometry: Optional geometry to sample from
    """
    print("=" * 60)
    print("IMAGE PROPERTIES")
    print("=" * 60)
    
    # Get all properties
    props = image.getInfo()
    
    # Band information
    bands = image.bandNames().getInfo()
    print(f"\nBands ({len(bands)} total):")
    print(f"  {bands[:5]}...{bands[-3:]}")  # Show first 5 and last 3
    
    # Get projection and scale info from first band
    projection = image.select([bands[0]]).projection()
    print(f"\nProjection:")
    print(f"  CRS: {projection.crs().getInfo()}")
    print(f"  Native scale: {projection.nominalScale().getInfo()} meters")
    
    # Get pixel type information
    band_types = image.bandTypes().getInfo()
    print(f"\nData Types:")
    for band, dtype in list(band_types.items())[:3]:  # Show first 3
        print(f"  {band}: {dtype['precision']}")
    
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
        print(f"\nBounds:")
        coords = bounds['coordinates'][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        print(f" Longitude: [{min(lons):.4f}, {max(lons):.4f}]")
        print(f" Latitude: [{min(lats):.4f}, {max(lats):.4f}]")
        
        # Approximate size
        lon_range = max(lons) - min(lons)
        lat_range = max(lats) - min(lats)
        width_km = lon_range * 111.32
        height_km = lat_range * 110.54
        print(f"\nApproximate dimensions:")
        print(f"  Width: ~{width_km:.1f} km")
        print(f"  Height: ~{height_km:.1f} km")
        
        # Estimate pixel dimensions at 10m resolution
        pixels_width = int(width_km * 1000 / 10)
        pixels_height = int(height_km * 1000 / 10)
        print(f" Pixels (at 10m): ~{pixels_width} x {pixels_height}")
        print(f" Total pixels: ~{pixels_width * pixels_height:,}")
        
        # estimate file size
        # 64 bands * 4 bytes per pixel (float32)
        size_bytes = pixels_width * pixels_height * 64 * 4
        size_mb = size_bytes / (1024**2)
        size_gb = size_bytes / (1024**3)
        print(f"\nEstimated uncompressed size:")
        if size_gb > 1:
            print(f"  ~{size_gb:.2f} GB")
        else:
            print(f"  ~{size_mb:.1f} MB")
    
    print("=" * 60)
    return None


def export_cloud(
    image: ee.Image,
    geometry: ee.Geometry,
    description: str,
    prefix: str,
    folder: str = 'EarthEngine',
    crs: str = 'EPSG:4326',
    cog: bool = False
) -> ee.batch.Task:
    """
    Export to Google Cloud Storage as GeoTIFF 
    (Cloud Optimised GeoTIFF as optional).
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
        scale=10,
        crs=crs,
        maxPixels=1e13,
        fileFormat='GeoTIFF',
        formatOptions={
            'cloudOptimized': cog
        }
    )
    
    task.start()
    print(f"Export started: {description}")
    print(f"Monitor at: https://code.earthengine.google.com/tasks")
    print(f"Will be saved to: /{folder}/{prefix}.tif")
    print("-" * 40)
    
    return task

def export_drive(
    image: ee.Image,
    geometry: ee.Geometry,
    description: str,
    prefix: str,
    folder: str = 'EarthEngine',
    crs: str = 'EPSG:4326',
    cog: bool = False
) -> ee.batch.Task:
    """
    Export to Google Drive as GeoTIFF 
    (Cloud Optimised GeoTIFF as optional).
    
    Args:
        image (ee.Image): Earth Engine image
        geometry (ee.Geometry): Region to export
        description (str): Export task name
        folder (str): Google Drive folder (main path)
        prefix (str): File name prefix 
            (can include nested folder path inside the main folder)
        crs (str): Output coordinate system reference system.
            Default is 'EPSG:4326'
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
        scale=10,
        crs=crs,
        maxPixels=1e13,
        fileFormat='GeoTIFF',
        formatOptions={
            'cloudOptimized': cog
        }
    )
    
    task.start()
    print(f"Export started: {description}")
    print(f"Monitor at: https://code.earthengine.google.com/tasks")
    print(f"Will be saved to: /{folder}/{prefix}.tif")
    
    return task

def wait_for_task(
    task: ee.batch.Task,
    poll_interval: int = 30
) -> dict:
    """
    Block until an EE task finishes and record runtime.

    Args:
        task: ee.batch.Task returned by Export
        poll_interval: seconds between status checks

    Returns:
        Dictionary with task metadata and timing info
    """
    start_time = time.time()
    task_id = task.id
    description = task.config.get('description', 'unknown')

    print(f"Waiting for task {description} ({task_id})")

    while True:
        status = task.status()
        state = status['state']

        if state in ['COMPLETED', 'FAILED', 'CANCELLED']:
            end_time = time.time()
            duration = end_time - start_time

            print(f"Task {description} finished with state={state}")
            print(f"Duration: {duration/60:.2f} minutes")

            return {
                'task_id': task_id,
                'description': description,
                'state': state,
                'start_time': start_time,
                'end_time': end_time,
                'duration_seconds': duration,
                'status': status
            }

        time.sleep(poll_interval)

def wait_for_all_tasks(tasks, poll_interval=60):
    """
    Wait until all Earth Engine tasks complete and record total runtime.
    
    Args:
        tasks: list of ee.batch.Task objects
        poll_interval: seconds to wait between status checks
    
    Returns:
        total_seconds: float, wall-clock seconds for all tasks
        all_statuses: list of full task metadata dicts
    """
    task_ids = [task.id for task in tasks]
    print(f"Waiting for {len(tasks)} tasks to complete...")
    start_time = time.time()
    
    all_completed = False
    while not all_completed:
        all_completed = True
        for task in tasks:
            status = task.status()
            state = status.get('state', 'UNKNOWN')
            if state not in ['COMPLETED', 'FAILED', 'CANCELLED']:
                all_completed = False
                break
        if not all_completed:
            time.sleep(poll_interval)
    
    end_time = time.time()
    total_seconds = end_time - start_time
    print(f"All tasks finished in {total_seconds:.1f} s")
    print("=" * 60)

    # capture full metadata
    all_statuses = []
    for task in tasks:
        status = task.status()
        for key in ['creation_timestamp_ms', 'start_timestamp_ms', 'update_timestamp_ms']:
            if key in status:
                status[key] = datetime.utcfromtimestamp(status[key]/1000).strftime('%Y-%m-%d %H:%M:%S')
        all_statuses.append(status)
    
    return total_seconds, all_statuses, task_ids

def get_operations_metadata(task_ids, verbose=False):
    """
    Given a list of task IDs, fetch and print operation metadata for each.
    """
    all_metadata = []
    
    for task_id in task_ids:
        operation = ee.data.getOperation(f'projects/earthengine-legacy/operations/{task_id}')
        # Convert timestamps to human-readable format
        for key in ['creation_timestamp_ms', 'start_timestamp_ms', 'update_timestamp_ms']:
            if key in operation:
                operation[key] = datetime.utcfromtimestamp(operation[key]/1000).strftime('%Y-%m-%d %H:%M:%S')
        all_metadata.append(operation)

    if verbose:
        print("--- All operation metadata ---")
        print(json.dumps(all_metadata, indent=4))
    return all_metadata

if __name__ == '__main__':

    verbose=False
    cog=False
    tiles="data/uk_20km_grid.gpkg"
    project="embed2social" # 'embed2social' or 'imago-479216' (non-commercial for testing)
    storage="cloud"
    folder="embed2social-storage"
    years = list(range(2021, 2022)) # range includes start, but excludes stop

    results = []
    overall_start = time.time()

    tasks = []

    _ = auth_init(project=project, auth_mode='notebook') # localhost' if running local .py (doesn't work in Docker)
    grid = geopandas.read_file(tiles)

    for _, row in grid.iterrows():
        cell = row["tile_name"]
        cell_gdf = grid.loc[[row.name]]
        
        for year in years:
            cell_gee_image, cell_geometry, count = build_cell_year(cell_gdf, year, verbose=False)
            
            # scaling image (int16)
            cell_gee_image = (
                cell_gee_image
                .clamp(-1.0, 1.0)
                .multiply(32767)
                .round()
                .toInt16()
            )
            
            if storage=="cloud":
                task = export_cloud(
                    image=cell_gee_image,
                    geometry=cell_geometry,
                    description=f"{cell}-{year}",
                    prefix=f"{year}/{cell}-{year}",
                    folder=folder,
                    crs="EPSG:27700",
                    cog=False
                )

            if storage=="drive":
                task = export_drive(
                    image=cell_gee_image,
                    geometry=cell_geometry,
                    description=f"{cell}-{year}",
                    prefix=f"{year}/{cell}-{year}",
                    folder=folder,
                    crs="EPSG:27700",
                    cog=False
                )

            if verbose:
                probe_image_properties(cell_gee_image)

            tasks.append(task)

            task_metadata = task.status()
            #print(json.dumps(task_metadata, indent=4))

    # wait for all tasks to complete   
    total_seconds, all_statuses, task_ids = wait_for_all_tasks(tasks, poll_interval=30)
    total_hours=total_seconds/3600
    print("=" * 60)
    print(f"Pipeline runtime: {total_seconds:.4f} seconds")
    print(f"  ↳ {total_hours:.4f} hours")

# fetch operation metadata and find total EECU time
all_ops_metadata = get_operations_metadata(task_ids, verbose=False)
total_eecu = 0.0

for op in all_ops_metadata:
    create_time = op.get('metadata', {}).get('createTime', 0)
    start_time = op.get('metadata', {}).get('startTime', 0)
    create_dt = datetime.fromisoformat(
        create_time.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    start_dt = datetime.fromisoformat(
        start_time.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    
    #print(f"Queue operation time: {(start_dt - create_dt).total_seconds()}")
    
    eecu = op.get('metadata', {}).get('batchEecuUsageSeconds', 0)
    total_eecu += eecu

total_eecu_hours=total_eecu/3600
print(f"Total billable EECU time across all tasks: {total_eecu:.4f} seconds")
print(f"  ↳ {total_eecu_hours:.4f} hours")
print("=" * 40)