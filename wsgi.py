import sys
import os

# ── PythonAnywhere WSGI entry point ──────────────────────────────────────────
# Edit USERNAME below to match your PythonAnywhere username.
project_home = '/home/yukaming/alpha-consensus'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Load .env file from the project directory
from dotenv import load_dotenv
load_dotenv(os.path.join(project_home, '.env'))

from app import app as application
