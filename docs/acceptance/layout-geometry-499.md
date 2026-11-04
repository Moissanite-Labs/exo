# Layout geometry acceptance (#499)

The inverse layout contract places the composer above the scrollable conversation stage inside the chat column. Its absolute screen Y coordinate depends on host chrome, fonts, and frame position. The previous input-top threshold of 120 pixels did not express that contract.

The browser tests now measure composer, input, stage, and column rectangles. They require containment and nonoverlapping order in inverse and classic modes, retain long-conversation newest-message scroll checks, and exercise two desktop widths including a deliberately shifted and resized host frame. The shifted inverse case also requires input Y greater than 120, demonstrating why the former threshold rejects a valid frame.

These tests run actual headless Chrome locally on macOS. They strengthen portable spatial acceptance but do not claim a Linux browser execution. No runtime CSS or layout behavior changes.
