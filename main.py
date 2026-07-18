import os
import traceback
from collections.abc import Callable
from datetime import datetime

from dotenv import load_dotenv

from logger import get_logger, setup_logging
from notify import send_failure_email
from process import process
from request import request
from retrieve import retrieve
from update import update

load_dotenv()

logger = get_logger("main")

# Collected (step_name, exception, traceback) for any step that failed.
failures: list[tuple[str, BaseException, str]] = []


def run_step(fn: Callable[[], None]) -> None:
    """Run a pipeline step, recording any failure but never raising.

    Steps run independently of each other's outcome, so one failing step
    does not stop the rest of the pipeline.
    """
    name = fn.__name__
    logger.info(f"=== Starting step: {name} ===")
    try:
        fn()
        logger.info(f"=== Step succeeded: {name} ===")
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"=== Step FAILED: {name}: {type(e).__name__}: {e} ===\n{tb}")
        failures.append((name, e, tb))


def main() -> None:
    os.makedirs("logs", exist_ok=True)
    log_file = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    setup_logging(log_file)

    run_step(request)
    run_step(retrieve)
    run_step(process)
    run_step(update)

    if failures:
        logger.warning(f"{len(failures)} step(s) failed; sending alert email")
        send_failure_email(failures, log_file)
    else:
        logger.info("All steps completed successfully")


if __name__ == "__main__":
    main()
