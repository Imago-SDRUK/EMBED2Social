import os
from datetime import datetime
import logging
import sys
import traceback

logger = logging.getLogger("imago")

def log_uncaught_exception(exc_type, exc_value, exc_traceback):
    """
    Handle all uncaught exceptions and log them to the configured logger.

    This function should be assigned to `sys.excepthook` so any
    exception not caught by try/except blocks is automatically logged.
    KeyboardInterrupt exceptions are passed through to allow normal program
    termination without logging.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        # Allow keyboard interrupts to exit normally
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    # Format traceback as string and log it
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.error(f"Uncaught exception:\n{tb}")

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

    # Redirect uncaught exceptions to logger (no nested function)
    sys.excepthook = log_uncaught_exception

def prints_to_logger(logger_name="imago", print_level=logging.DEBUG):
    """
    Redirect all print() calls and Python warnings to a logger.
    
    Prints -> logger.info
    Warnings (Python warnings, e.g., NotGeoreferencedWarning) -> logger.warning
    """
    logger = logging.getLogger(logger_name)

    # Redirect standard print() statements to the specified logging level
    sys.stdout.write = lambda msg: logger.log(print_level, msg.rstrip()) if msg.rstrip() else None
    # Capture Python warnings (rasterio) and send to logger.warning
    logging.captureWarnings(True)