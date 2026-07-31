# 29.07.26

from VibraVid.tui.app import main
from VibraVid.utils.frozen import fix_ld_library_path

fix_ld_library_path()

if __name__ == "__main__":
    main()

