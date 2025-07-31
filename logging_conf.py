import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("invoicebot.log", encoding="utf-8")
    ]
)

logger = logging.getLogger("invoice-backend")
