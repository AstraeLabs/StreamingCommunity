# 10.01.26

import os
import re
import xml.etree.ElementTree as et
from typing import Optional, List
from pathlib import Path

from rich.console import Console
from ttconv.imsc.reader import to_model
from ttconv.srt.writer import from_model

console = Console()


def convert_ttml_to_srt(ttml_path: str, srt_path: Optional[str] = None) -> bool:
    """
    Convert TTML file or .m4s fragment containing TTML to SRT format.
    Uses ttconv for high-fidelity conversion.

    Args:
        ttml_path (str): Path to the TTML or .m4s file.
        srt_path (Optional[str]): Path where to save the SRT file.
                                 If None, uses same name as ttml_path but with .srt extension.

    Returns:
        bool: True if conversion was successful, False otherwise.
    """
    if not os.path.exists(ttml_path):
        console.print(f"[red]File {ttml_path} does not exist")
        return False

    if srt_path is None:
        srt_path = str(Path(ttml_path).with_suffix('.srt'))

    try:
        with open(ttml_path, 'rb') as f:
            data = f.read()

        # Extract all TTML blocks (works for both plain TTML files and .m4s fragments)
        ttml_blocks = re.findall(br'<\?xml.*?</tt>', data, re.DOTALL)

        if not ttml_blocks:
            # Try to see if it's a plain TTML without the XML declaration or just one block
            try:
                text_content = data.decode('utf-8')
                if '<tt' in text_content and '</tt>' in text_content:
                    match = re.search(r'<tt.*?</tt>', text_content, re.DOTALL)
                    if match:
                        ttml_blocks = [match.group(0).encode('utf-8')]
            except Exception:
                pass

        if not ttml_blocks:
            console.print(f"[red]No valid TTML blocks found in {ttml_path}")
            return False

        all_captions: List[str] = []
        processed_blocks = 0
        skipped_blocks = 0

        for block in ttml_blocks:
            try:

                # Decode the TTML block
                ttml_str = block.decode('utf-8')

                # Parse the TTML string into an ElementTree
                root = et.fromstring(ttml_str)
                tree = et.ElementTree(root)

                # Convert TTML to internal model
                model = to_model(tree)

                if model is not None:
                    srt_content = from_model(model)
                    if srt_content.strip():
                        all_captions.append(srt_content.strip())
                    processed_blocks += 1
                else:
                    skipped_blocks += 1

            except Exception:
                skipped_blocks += 1
                continue

        if not all_captions:
            console.print(f"[red]No valid TTML blocks processed from {ttml_path}")
            return False

        # Combine all SRT subtitles
        # Note: If multiple blocks are present, they might have overlapping or duplicate indices.
        # Joining them as a single string is simple, but we might want to ensure sequence continuity.
        # For now, we follow the requested logic.
        srt_output = "\n\n".join(all_captions)

        # Save the SRT file
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_output)

        return True

    except Exception as e:
        console.print(f"[red]Error during TTML to SRT conversion: {e}")
        return False

def extract_srt_from_m4s(m4s_file_path: str, output_srt_path: Optional[str] = None) -> str:
    """
    Compatibility wrapper for the user requested function name.
    """
    if convert_ttml_to_srt(m4s_file_path, output_srt_path):
        if output_srt_path is None:
            output_srt_path = str(Path(m4s_file_path).with_suffix('.srt'))
        with open(output_srt_path, 'r', encoding='utf-8') as f:
            return f.read()
    else:
        raise ValueError("Failed to extract SRT from m4s")