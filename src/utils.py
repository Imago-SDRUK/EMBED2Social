import os
from datetime import datetime
import logging

logger = logging.getLogger("imago")

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