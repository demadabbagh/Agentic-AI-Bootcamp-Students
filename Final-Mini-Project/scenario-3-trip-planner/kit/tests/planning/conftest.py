"""Make the kit folder importable (planning/, services/) however pytest is started."""
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))
