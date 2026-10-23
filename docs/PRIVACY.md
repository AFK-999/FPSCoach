# FPS Coach (Vantage) Privacy Policy

Last updated: 2026-10-19 (rev49)

This policy describes what FPS Coach stores, what it sends, and what it never does.
The same statements appear in the app on the Privacy page.

## Data kept on your machine

- **Training history** is saved to `.fpscoach_analytics.json` in the project folder.
  The Privacy page can delete it.
- **Preferences** (including the crosshair design and overlay settings) are stored in
  your system's settings store.
- **Chat transcript** is kept only for the current session. Clearing the chat on the
  Privacy page removes it.

## Screen capture

- Capture frames are processed in memory for the app's own analysis and are not written
  to disk.
- The capture window list reads window titles and process names only, so you can choose
  a window. It does not read or write any process's memory.

## Crosshair overlay

- The overlay is **off by default**.
- When it is on, or while you edit its position, it draws a crosshair on your primary
  screen only.
- **The active crosshair overlay does not interact with or inject into any game
  processes.** It does not read game frames, hook input, or read or write game memory.
- When locked, the overlay ignores the mouse, so clicks reach the game.
- The overlay position is saved on your machine.

## Network

- AI requests are sent only to the endpoint you set in Settings (for example, a local
  Ollama server).
- Voice recognition can run fully offline with a local model.

## Features that are not offered

- Gameplay-based feedback drawn over a game.
- Detection of enemy targets or shooting from game footage.

These were requested and then declined, because they would give a player an advantage
in live play and break the rules of games that use anti-cheat systems. They are not
built, and this policy does not describe them as features.
