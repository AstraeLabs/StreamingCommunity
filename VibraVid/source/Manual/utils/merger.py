# 19.05.25

import os
import logging

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


class FileMerger:
    @staticmethod
    def merge(segment_dir, output_file):
        try:
            init_file = os.path.join(segment_dir, 'init.m4s')
            segments = sorted([
                os.path.join(segment_dir, f)
                for f in os.listdir(segment_dir)
                if f.startswith('seg_') and f.endswith('.m4s')
            ])
            
            with open(output_file, 'wb') as outfile:
                if os.path.exists(init_file):
                    with open(init_file, 'rb') as f:
                        outfile.write(f.read())
                
                for seg_file in segments:
                    with open(seg_file, 'rb') as f:
                        outfile.write(f.read())
            return True
            
        except Exception as e:
            console.print(f"[red]Merge failed: {e}.")
            return False