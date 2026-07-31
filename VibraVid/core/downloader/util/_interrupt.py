# 22.02.25

import signal
import time

from rich.console import Console

console = Console()


class InterruptHandler:
    def __init__(self) -> None:
        self.interrupt_count: int = 0
        self.last_interrupt_time: float = 0.0
        self.kill_download: bool = False
        self.force_quit: bool = False

    def handle(self, signum, frame, original_handler) -> None:
        """Signal callback — attach with ``partial(self.handle, original_handler=prev)``."""
        now = time.time()
        if now - self.last_interrupt_time > 2:
            self.interrupt_count = 0

        self.interrupt_count += 1
        self.last_interrupt_time = now

        if self.interrupt_count == 1:
            self.kill_download = True
            console.print("\n[yellow]First interrupt received. Download will complete and save. Press Ctrl+C three times quickly to force quit.")

        elif self.interrupt_count >= 3:
            self.force_quit = True
            console.print("\n[red]Force quit activated. Saving partial download...")
            signal.signal(signum, original_handler)
