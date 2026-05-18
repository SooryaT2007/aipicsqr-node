"""
Logger setup for the Photographer Node.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_KEEP_DAYS = 7


def setup_logger(name: str, log_dir: str = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s │ %(levelname)-7s │ %(message)s',
        datefmt='%H:%M:%S',
    ))
    logger.addHandler(console_handler)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # Rotates at midnight; keeps 7 days, deletes the rest automatically.
        file_handler = TimedRotatingFileHandler(
            filename=log_path / 'node.log',
            when='midnight',
            backupCount=_LOG_KEEP_DAYS,
            encoding='utf-8',
        )
        file_handler.suffix = '%Y%m%d'
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s │ %(levelname)-7s │ %(module)s │ %(message)s',
        ))
        logger.addHandler(file_handler)

    return logger
