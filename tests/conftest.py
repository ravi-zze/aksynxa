import sys
from pathlib import Path

# Project root ko path me daalo taaki `import app...` chale
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
