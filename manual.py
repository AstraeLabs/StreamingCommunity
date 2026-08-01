# 26.11.24
# ruff: noqa: E402

from VibraVid.utils.frozen import fix_ld_library_path

fix_ld_library_path()

from VibraVid.cli.run import main

main()
