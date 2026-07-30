# Packaging VibraVid TUI

This document covers packaging considerations for the VibraVid TUI (Text User Interface) built with Textual.

## PyInstaller Configuration

When building VibraVid with PyInstaller, Textual requires additional hidden imports to function correctly.

### Required Hidden Imports

Textual uses dynamic imports for widgets, screens, and themes. Add these to your PyInstaller spec file or command:

```python
hiddenimports=[
    # Textual core
    'textual',
    'textual.app',
    'textual.binding',
    'textual.css',
    'textual.css.query',
    'textual.css.styles',
    'textual.css.tokenize',
    'textual.dom',
    'textual.driver',
    'textual.events',
    'textual.geometry',
    'textual.message',
    'textual.message_pump',
    'textual.reactive',
    'textual.screen',
    'textual.scrollbar',
    'textual.strip',
    'textual.timer',
    'textual.widget',
    
    # Textual widgets
    'textual.widgets',
    'textual.widgets._cell',
    'textual.widgets._data_table',
    'textual.widgets._footer',
    'textual.widgets._header',
    'textual.widgets._label',
    'textual.widgets._list_view',
    'textual.widgets._markdown',
    'textual.widgets._markdown_viewer',
    'textual.widgets._placeholder',
    'textual.widgets._pretty',
    'textual.widgets._progress_bar',
    'textual.widgets._radio_set',
    'textual.widgets._rich_log',
    'textual.widgets._rule',
    'textual.widgets._select',
    'textual.widgets._sparkline',
    'textual.widgets._static',
    'textual.widgets._switch',
    'textual.widgets._tabs',
    'textual.widgets._text_area',
    'textual.widgets._tooltip',
    'textual.widgets._tree',
    
    # VibraVid TUI modules
    'VibraVid.tui',
    'VibraVid.tui.app',
    'VibraVid.tui.bridge',
    'VibraVid.tui.screens',
    'VibraVid.tui.screens.main',
    'VibraVid.tui.screens.queue',
    'VibraVid.tui.screens.history',
    'VibraVid.tui.screens.settings',
    'VibraVid.tui.screens.system',
    'VibraVid.tui.widgets',
]
```

### Example PyInstaller Command

```bash
pyinstaller --onefile \
  --hidden-import=textual \
  --hidden-import=textual.widgets \
  --hidden-import=VibraVid.tui \
  --hidden-import=VibraVid.tui.app \
  --collect-all=textual \
  manual.py
```

### Example .spec File

```python
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['manual.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('VibraVid/tui/theme.tcss', 'VibraVid/tui/'),
    ],
    hiddenimports=[
        'textual',
        'textual.app',
        'textual.widgets',
        'textual.screen',
        'VibraVid.tui',
        'VibraVid.tui.app',
        'VibraVid.tui.bridge',
        'VibraVid.tui.screens',
        'VibraVid.tui.widgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='VibraVid',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Required for TUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

## Important Notes

### Console Mode

The TUI requires a terminal/console. Always use `console=True` in PyInstaller EXE configuration. Windowed mode (`console=False`) will not work with Textual applications.

### TCSS Files

Textual uses CSS-like stylesheets (`.tcss` files). These must be included as data files:

```python
datas=[
    ('VibraVid/tui/*.tcss', 'VibraVid/tui/'),
]
```

### Terminal Size

The TUI is designed to work with terminals as small as 60x20 characters. Test your packaged binary with small terminal sizes to ensure proper rendering.

### Platform-Specific Notes

#### Windows
- Windows Terminal is recommended over cmd.exe for better Unicode support
- Ensure the console font supports Unicode characters

#### macOS
- No special considerations

#### Linux
- Ensure the terminal supports 256 colors for best appearance
- Works in both X11 and Wayland terminals

#### Termux (Android)
- Install via `termux_install.sh` which handles all dependencies
- Textual works natively in Termux terminal
- No X11 package required (TUI is terminal-based, not GUI)

## Testing Packaged Binary

After building, test the TUI with:

```bash
# Normal terminal
./VibraVid

# Small terminal (resize to 60x20 or use tmux split)
./VibraVid

# Verify TUI launches without errors
```

## Troubleshooting

### ModuleNotFoundError: No module named 'textual'

Add `--collect-all=textual` to your PyInstaller command to ensure all Textual modules are included.

### TUI renders but styles are missing

Ensure `.tcss` files are included in the `datas` section of your spec file.

### Binary works in large terminal but crashes in small terminal

Check that your TUI code handles small terminal sizes gracefully. Use `size=(60, 20)` in tests to verify.