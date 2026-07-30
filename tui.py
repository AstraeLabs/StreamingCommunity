# 29.07.26
# ruff: noqa: E402

from VibraVid.utils.frozen import fix_ld_library_path
fix_ld_library_path()

from VibraVid.tui.app import main

main()
