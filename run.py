import sys
from pathlib import Path

# add src to import path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from startupcan.main import main

if __name__ == "__main__":
    main()
