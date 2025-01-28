import sys
from loguru import logger as logging
logging.remove()
logging.add("logs.log", format="<white>{time:YYYY-MM-DD HH:mm:ss}</white>"
                               " | <level>{level: <8}</level>"
                               " | <cyan><b>{line}</b></cyan>"
                               " - <white><b>{message}</b></white>")

# Add handler to log to stdout (console)
logging.add(sys.stdout, format="<white>{time:YYYY-MM-DD HH:mm:ss}</white>"
                               " | <level>{level: <8}</level>"
                               " | <cyan><b>{line}</b></cyan>"
                               " - <white><b>{message}</b></white>")

logger = logging.opt(colors=True)