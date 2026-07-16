import os
from datetime import datetime
from time import sleep

from dotenv import load_dotenv

from logger import setup_logging
from process import process
from request import request
from retrieve import retrieve
from update import update

load_dotenv()

os.makedirs("logs", exist_ok=True)
log_file = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
setup_logging(log_file)

request()
sleep(5)
retrieve()
process()
update()
