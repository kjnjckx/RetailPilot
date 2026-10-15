"""A small subprocess used to verify SQLite queue claims across independent workers."""

import argparse
import json
from pathlib import Path

from backend import storage as db

parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--owner", required=True)
args = parser.parse_args()
db.DB_PATH = Path(args.database)
db.initialize()
print(json.dumps(db.claim_task(args.owner)))
