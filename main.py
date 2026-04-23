import os
from datetime import datetime
from logger import setup_logging

os.makedirs("logs", exist_ok=True)
log_file = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
setup_logging(log_file)

from request import request
from retrieve import retrieve
from process import process

request()
retrieve()
process()
