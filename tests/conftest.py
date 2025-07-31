import sys
import os
import pytest
from fastapi.testclient import TestClient

# Добавляем корень проекта в sys.path (где лежит main.py)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
