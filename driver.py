import subprocess
import logging

# config log
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_cmd(cmd):
    """run Shell cmd and check sudo"""
    try:
        logger.debug(f"Executing: {cmd}")
        full_cmd = f"sudo {cmd}"
        subprocess.run(full_cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {cmd}\nError: {e.stderr.decode().strip()}")
        return False
