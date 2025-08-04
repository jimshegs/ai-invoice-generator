# tests/conftest.py
from pathlib import Path
import sys
import pytest

# Add project root (folder that contains app.py) to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app as flask_app  # import the Flask app instance

@pytest.fixture
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()
