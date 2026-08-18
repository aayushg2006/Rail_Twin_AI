import sys
from pathlib import Path

# Ensure `import app...` resolves when running pytest from anywhere.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
