# FPS Coach (Vantage) Privacy Policy

Last updated: 2026-10-23 (rev74)

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

- The live capture preview is processed in memory for the app's own calibration analysis.
- **Recording (Capture tab, Recording card) is off unless you start it.** While it runs it
  writes a video of the **primary display**, either to your temp folder or, if you tick
  **Keep the recording**, to the folder you choose (`Videos/FPSCoach` by default).
  - Without **Keep**, the file is deleted when you press Stop or close the app.
  - With **Keep**, the file is yours: it stays in that folder until you delete it,
    including across app restarts, and it is named `fpscoach-recording-<date>-<time>.mp4`
    so you can find it later.
  - Recordings are never uploaded by the app. Anything you do with the file afterwards
    (send it, sync it, open it elsewhere) is up to you.
- The recording's analysis timer samples frame statistics (size and mean brightness) as a
  pipeline health check. It does not look for targets, shooting, or gameplay events.
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

Recording is available and user-controlled: it starts when you press Record, stops when
you press Stop or close the app, and only outlives the session if you ask it to. It is a
file for your own review, not part of any in-play assistance.
