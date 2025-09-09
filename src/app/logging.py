from loguru import logger
import sys

logger.remove()
logger.add(sys.stdout, level="INFO", backtrace=True, diagnose=False, enqueue=True)
