import sys
import traceback
from pathlib import Path

# add src to import path
sys.path.insert(0, str(Path(__file__).parent / "src"))


if __name__ == "__main__":
    try:
        from startupcan.main import main
        exit_code = main()

        print("\n[INFO] Finished.")
        input("Press ENTER to close...")

        sys.exit(exit_code)

    except Exception as e:
        print("\n" + "=" * 80)
        print("[ERROR]")
        traceback.print_exc()
        print(str(e))
        print("=" * 80 + "\n")
        input("Press ENTER to close...")
        sys.exit(1)
