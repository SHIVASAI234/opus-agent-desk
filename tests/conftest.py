import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from opus_desk.config import load_config  # noqa: E402


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config())

