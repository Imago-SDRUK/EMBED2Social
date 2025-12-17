import ee
import json
import geopandas
from typing import Optional, List, Tuple

def auth_init(project: str ='ee-darribas-embeddings'):
    """Start session on GEE"""
    ee.Authenticate(auth_mode='notebook', force=True)
    try:
        ee.Initialize(project=project)
    except:
        # Code from Claude to circumvent not being able to authenticate the easy way
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        import os
        # Load credentials manually
        credentials_path = os.path.expanduser('~/.config/earthengine/credentials')
        with open(credentials_path, 'r') as f:
            creds_data = json.load(f)
        # Create credentials object
        credentials = Credentials(
            token=None,
            refresh_token=creds_data.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret')
        )
        # Initialize with explicit credentials
        ee.Initialize(credentials=credentials, project='ee-darribas-embeddings')
    return print(ee.String('Hello from the Earth Engine servers!').getInfo())

def build_cell_year(
    cell: str,
    year: int,
    tgt_crs: Optional[str] = None
) -> Tuple[ee.Image, ee.Geometry]:
    """
    Build a GEE Image with embeddings from year `year` in grid cell `cell`.

    Args:
        cell: cell code within the OSGB grid
        year: year for which embeddings are required
        tgt_crs: [default to None] CRS in which `image` is returned
    """
    geometry = ee.Geometry(
        json.loads(
            cell.to_crs('EPSG:4326').geometry.to_json()
        )['features'][0]['geometry']
    )
    # Load the satellite embedding collection
    collection = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
    # Filter by date and region
    start_date = f'{year}-01-01'
    end_date = f'{year+1}-01-01'
    # Get all images that overlap the geometry
    filtered = collection.filterDate(start_date, end_date).filterBounds(geometry)
    count = filtered.size().getInfo()
    if count == 0:
        raise ValueError(f"No images found for year {year} in specified region")
    # Create mask for the geometry
    roi_mask = ee.Image(1).clip(geometry)
    # Apply mask to all images
    masked = filtered.map(lambda img: img.updateMask(roi_mask))
    # Intelligently handle single vs multiple tiles
    if count == 1:
        print(f"  Single tile covers this region")
        image = masked.first()
    else:
        print(f"  Mosaicking {count} tiles")
        image = masked.mosaic()
    # Clip to geometry
    image = image.clip(geometry)
    # Reproject if output CRS specified
    if tgt_crs:
        image = image.reproject(crs=output_crs, scale=10)
    return image, geometry

def probe_image_properties(image: ee.Image, geometry: ee.Geometry = None):
    """
    Extract comprehensive information about an ee.Image.
    
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
        print(f"  Longitude: [{min(lons):.4f}, {max(lons):.4f}]")
        print(f"  Latitude: [{min(lats):.4f}, {max(lats):.4f}]")
        
        # Approximate size
        lon_range = max(lons) - min(lons)
        lat_range = max(lats) - min(lats)
        width_km = lon_range * 111.32  # Approximate
        height_km = lat_range * 110.54  # Approximate
        print(f"\nApproximate Dimensions:")
        print(f"  Width: ~{width_km:.1f} km")
        print(f"  Height: ~{height_km:.1f} km")
        
        # Estimate pixel dimensions at 10m resolution
        pixels_width = int(width_km * 1000 / 10)
        pixels_height = int(height_km * 1000 / 10)
        print(f"  Pixels (at 10m): ~{pixels_width} x {pixels_height}")
        print(f"  Total pixels: ~{pixels_width * pixels_height:,}")
        
        # Estimate file size
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


def download_as_cog_via_drive(
    image: ee.Image,
    geometry: ee.Geometry,
    description: str,
    folder: str = 'EarthEngine',
    crs: str = 'EPSG:4326'
) -> ee.batch.Task:
    """
    Export as Cloud-Optimized GeoTIFF (COG) to Google Drive.
    This is the RECOMMENDED method for most cases.
    
    Args:
        image: Earth Engine image
        geometry: Region to export
        description: Export task name
        folder: Google Drive folder
        crs: Output coordinate system
        
    Returns:
        Export task
    """
    task = ee.batch.Export.image.toDrive(
        image=image,
        description=description,
        folder=folder,
        fileNamePrefix=description,
        region=geometry,
        scale=10,
        crs=crs,
        maxPixels=1e13,
        fileFormat='GeoTIFF',
        formatOptions={
            'cloudOptimized': True  # This makes it a COG!
        }
    )
    
    task.start()
    print(f"COG export started: {description}")
    print(f"Monitor at: https://code.earthengine.google.com/tasks")
    print(f"Will be saved to Google Drive/{folder}/{description}.tif")
    
    return task

if __name__ == '__main__':
    _ = auth_init()
    grid = geopandas.read_file('uk_20km_grid.gpkg')
    liv_cells = ['SJ28', 'SJ48']
    liv = grid[grid['tile_name'].isin(liv_cells)]
    years = list(range(2017, 2025))
    for cell in liv_cells:
        for year in years:
            cell_gee_image, cell_geometry = build_cell_year(
                liv.query(f'tile_name == "{cell}"'), year
            )
            #probe_image_properties(cell_gee_image)
            task = download_as_cog_via_drive(
                cell_gee_image,
                cell_geometry,
                f"{cell}-{year}",
                "satellite_embeddings_v1",
                "EPSG:27700"
            )