"""Read a named run configuration from the project's configs folder."""
import json
from pathlib import Path


def get_config(name):
    return json.loads((Path(__file__).resolve().parents[2] / 'configs' / f'{name}.json').read_text(encoding='utf-8'))
