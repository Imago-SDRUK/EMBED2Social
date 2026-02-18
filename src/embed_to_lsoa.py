"""
Small script to extract LSOA zonal statistics from embeddings files.

Reads a newline-delimited list of raster file paths from stdin,
loads shared config.yaml, starts a Dask client (LocalCluster or SLURMCluster),
runs extraction, writes a single Parquet file, and prints its path to stdout.
"""

import os
import sys
import yaml
from imago.io.lsoa_extraction import extract_lsoa_zonal_stats
import argparse


def parse_args():
    """Load in the config filepath from the command line,
    or set it to `config.yaml`"""
    parser = argparse.ArgumentParser(
        prog="Imago",
        description="Processing of cloud probability for the Imago data products",
    )
    parser.add_argument(
        "--config_path",
        "-i",
        help="Absolute or relative path to a config.yaml file",
        default="config.yaml",
        required=False,
    )
    return parser.parse_args()


def validate_config(input_cfg, output_cfg, dask_cfg):
    """Check that fields in the config files have acceptable values"""

    # Dask configuration
    # set client_worker_timeout to None by default (i.e. it will wait
    # until we have n/4 workers indefinitely.
    if "client_worker_timeout" not in dask_cfg:
        dask_cfg["client_worker_timeout"] = None
    # pass-by-reference so don't need to return


def setup_dask_cluster(dcfg):
    """
    Setup a Dask cluster based on the provided configuration.
    For this quick and dirty embeddings script for the VM, we
    only use LocalCluster.

    Parameters
    ----------
    dcfg : dict
        Dask configuration dictionary containing:
        - local_cluster (bool): Whether to use a local cluster.
        - n_workers (int): Number of workers.
        - threads_per_worker (int): Threads per worker.
        - memory (str): Memory limit per worker.
        - cores (int): Number of cores per worker (for SLURM).
        - worker_walltime (str): Walltime for each worker (for SLURM).
        - job_extra_directives (list): Additional SLURM job directives.
    """
    from dask.distributed import Client, LocalCluster

    cluster = LocalCluster(
        n_workers=dcfg["n_workers"],
        threads_per_worker=dcfg["threads_per_worker"],
        memory_limit=dcfg["memory_limit"],
        dashboard_address=dcfg.get("dashboard_address", ":8787"),
    )
    return Client(cluster)


def main(files, config):

    # Load in and validate config
    input_cfg = config["input"]
    output_cfg = config["output"]
    dask_cfg = config["dask"]
    validate_config(input_cfg, output_cfg, dask_cfg)

    # dask client
    client = setup_dask_cluster(dask_cfg)

    # get LSOA shapefile path from config or env var
    lsoa_path = os.path.join(
        input_cfg.get("input_storage"), input_cfg.get("lsoa_shapefile")
    ) or os.environ.get("LSOA_SHAPEFILE_PATH")
    if not lsoa_path:
        raise ValueError(
            "No LSOA geography file provided. Set lsoa.geography_file in config.yaml "
            "or export LSOA_SHAPEFILE_PATH."
        )

    year = input_cfg["time"]["start_date"][:4]  # assume ISO string
    # output parquet path: either
    # config["output"]["lsoa_output_filepath"], else a default for the year in question
    out_path = output_cfg.get("lsoa_output_file") or os.path.join(
        os.path.dirname(files[0]), f"lsoa_zonal_stats_{year}.parquet"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    print("out_path = ", out_path)
    output_fmt = output_cfg.get("lsoa_output_format")
    scaling_factor = output_cfg.get("scaling_factor")
    # run extraction - dask-geopandas means this is implicitly delayed/parallelised
    _ = extract_lsoa_zonal_stats(
        tiles_path=files,
        lsoa_path=lsoa_path,
        output_path=out_path,
        output_fmt=output_fmt,
        scaling_factor=scaling_factor,
    )

    return out_path

if __name__ == "__main__":

    # get filenames from stdin
    files = [ln.strip() for ln in sys.stdin if ln.strip()]
    if not files:
        raise ValueError("No input filenames provided via stdin.")

    args = parse_args()
    with open(args.config_path, "r") as f:
        config = yaml.safe_load(f)
    import time

    start = time.time()
    out_path = main(files, config)
    print("Elapsed seconds = ", time.time() - start)
    print("File written to: ", out_path)

