# 06.06.25

import json
import os
import sys
import tempfile

from VibraVid.utils import config_manager
from VibraVid.source.style.tracker import download_tracker
from VibraVid.cli.run import execute_hooks


def main():
    with tempfile.TemporaryDirectory() as tmp:
        out_file = os.path.join(tmp, "hook_out.txt")
        script_path = os.path.join(tmp, "hook_script.py")
        expected_path = os.path.join(tmp, "completed file.mkv")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "import json\n"
                "import os\n"
                "import sys\n"
                "payload = {\n"
                "    'stage': os.environ.get('SC_HOOK_STAGE'),\n"
                "    'path': os.environ.get('SC_DOWNLOAD_PATH'),\n"
                "    'status': os.environ.get('SC_DOWNLOAD_STATUS'),\n"
                "    'success': os.environ.get('SC_DOWNLOAD_SUCCESS'),\n"
                "    'args': sys.argv[1:],\n"
                "}\n"
                "with open(os.environ.get('HOOK_OUT'), 'a', encoding='utf-8') as fp:\n"
                "    fp.write(json.dumps(payload) + '\\n')\n"
            )

        original_hooks = (
            config_manager.config.get("HOOKS", {}).copy()
            if config_manager.config.get("HOOKS")
            else {}
        )

        try:
            # Configure hooks: run the python script pre and post
            config_manager.config.setdefault("HOOKS", {})
            config_manager.config["HOOKS"]["pre_run"] = [
                {
                    "name": "test-pre",
                    "type": "python",
                    "path": script_path,
                    "env": {"HOOK_OUT": out_file},
                    "enabled": True,
                    "continue_on_error": False,
                }
            ]
            config_manager.config["HOOKS"]["post_download"] = [
                {
                    "name": "test-post-download",
                    "type": "python",
                    "path": script_path,
                    "args": ["{download_path}", "{download_id}"],
                    "env": {"HOOK_OUT": out_file},
                    "enabled": True,
                    "continue_on_error": False,
                }
            ]

            # Execute and assert
            execute_hooks("pre_run")
            download_tracker.start_download("dl-1", "Example", "test-site", "Film", path=expected_path)
            download_tracker.complete_download("dl-1", success=True, path=expected_path)

            with open(out_file, "r", encoding="utf-8") as fp:
                content = [json.loads(line) for line in fp.read().splitlines() if line.strip()]

            assert len(content) == 2, f"Unexpected hook count: {content!r}"
            assert content[0]["stage"] == "pre_run", f"Unexpected pre hook payload: {content[0]!r}"
            assert content[1]["stage"] == "post_download", f"Unexpected post hook payload: {content[1]!r}"
            assert content[1]["path"] == os.path.abspath(expected_path), f"Unexpected path: {content[1]!r}"
            assert content[1]["args"] == [os.path.abspath(expected_path), "dl-1"], f"Unexpected args: {content[1]!r}"
            assert content[1]["success"] == "1", f"Unexpected success flag: {content[1]!r}"

            print("OK: hooks executed with download context")

        finally:
            # Restore original hooks configuration
            if original_hooks:
                config_manager.config["HOOKS"] = original_hooks
            else:
                config_manager.config.pop("HOOKS", None)


if __name__ == "__main__":
    sys.exit(main())