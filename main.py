import os
from datetime import datetime
from logger import setup_logging
from dotenv import load_dotenv
from time import sleep

load_dotenv()

os.makedirs("logs", exist_ok=True)
log_file = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
setup_logging(log_file)

from request import request
from retrieve import retrieve
from process import process
from update import update

request()
sleep(5)
retrieve()
process()
update()
