# Hook System

Execute custom scripts at specific points in the download lifecycle. Hooks are configured in `config.json` under the `HOOKS` key.

**Available stages:**
- `pre_run` — runs before the main flow starts
- `pre_download` — runs once per individual item, right before that movie/episode starts downloading
- `post_download` — runs after each individual download completes (in the GUI, once per item)
- `post_run` — runs once when the overall execution ends

```json
{
  "HOOKS": {
    "pre_run": [
      {
        "name": "prepare-env",
        "type": "python",
        "path": "scripts/prepare.py",
        "args": ["--clean"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux", "darwin"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "pre_download": [
      {
        "name": "pre-download-env",
        "type": "python",
        "path": "/app/script.py",
        "args": ["{download_title}"],
        "timeout": 30,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_download": [
      {
        "name": "post-download-env",
        "type": "python",
        "path": "/app/script.py",
        "args": ["{download_path}"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_run": [
      {
        "name": "notify",
        "type": "bash",
        "command": "echo 'Download finished: {download_title}'",
        "allow_inline_template": true
      }
    ]
  }
}
```

## Hook Options

| Key | Description |
|-----|-------------|
| `name` | Descriptive label for the hook |
| `type` | Script type: `python`, `bash`, `sh`, `shell`, `bat`, `cmd` |
| `path` | Path to script file (alternative to `command`) |
| `command` | Inline command to execute (alternative to `path`). Note: `args` are ignored when using `command` |
| `allow_inline_template` | If `true`, expand `{download_title}`-style placeholders inside `command` itself (off by default — `command` is otherwise run verbatim, unlike `path`/`args`/`cwd` which always expand placeholders) |
| `args` | List of arguments passed to the script |
| `env` | Additional environment variables as key-value pairs |
| `cwd` | Working directory for execution (supports `~` and env vars) |
| `os` | Optional OS filter: `["windows"]`, `["darwin"]`, `["linux"]`, or any combination |
| `timeout` | Maximum execution time in seconds (hook fails if exceeded) |
| `enabled` | Enable or disable the hook without removing it |
| `continue_on_error` | If `false`, stops execution when the hook fails |

## Hook Types

- **Python:** runs with the current Python interpreter
- **Bash / sh / shell:** executed via `/bin/bash -c` on macOS/Linux
- **Bat / cmd / shell:** executed via `cmd /c` on Windows
- **Inline commands:** use `command` instead of `path` for simple one-liners

## Shared Download Cache (Vault)

The `HOOKS` block also holds two keys that are **not** a script hook: `db_store` and
`db_info`. Together they control an opt-in shared cache of already-processed (downloaded,
decrypted, muxed) files, keyed by title/type/season/episode:

```json
{
  "HOOKS": {
    "db_store": false,
    "db_info": {
      "url": "",
      "token": "",
      "skip_if_cached": false
    }
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `db_store` | `false` | Master switch for the shared cache. Has no effect unless `db_info.url` is also set — with an empty `db_info` (the shipped default) the feature is completely inert |
| `db_info.url` | — | Base URL of the vault service to query/upload to. Required for **any** interaction, fetch or upload |
| `db_info.token` | — | Upload authorization token. Without it VibraVid is **fetch-only**: it can still look up and download cache hits, but never uploads what it downloads |
| `db_info.skip_if_cached` | `false` | Stricter mode: if a vault hit exists, **skip the item entirely** instead of fetching it — no file is produced locally at all for that movie/episode this run. Off by default, since normally a cache hit should still get you the file |

Even with `db_store: true` and a valid `db_info.token`, **uploads only happen for services that
opt in**: the site module's `__init__.py` must set `_db_upload = True`. This is separate from
`db_info.token` being set — the token controls *authorization*, `_db_upload` controls whether
that particular service is even allowed to try. A handful of services ship with this set; most
don't, so they stay fetch-only regardless of `db_info` configuration.


## Context Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{download_path}` | Absolute path of the downloaded file |
| `{download_dir}` | Directory containing the downloaded file |
| `{download_filename}` | Filename of the downloaded file |
| `{download_id}` | Internal download identifier |
| `{download_title}` | Download title |
| `{download_site}` | Source site name |
| `{download_media_type}` | Media type |
| `{download_status}` | Final download status |
| `{download_error}` | Error message, if any |
| `{download_success}` | `1` on success, `0` on failure |
| `{stage}` | Current hook stage |

The same values are also exposed as environment variables with the `SC_` prefix (e.g. `SC_DOWNLOAD_PATH`, `SC_DOWNLOAD_SUCCESS`, `SC_HOOK_STAGE`).
