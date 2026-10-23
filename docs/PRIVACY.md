# FPS Coach (Vantage) Privacy Policy

Last updated: 2026-10-23 (rev79)

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

## Live vision (AI Coach)

- **Off at every launch.** The switch is not saved; the next start begins with it off,
  exactly like In-Game Mode.
- When it is on, Vantage sends **downscaled still frames of your capture preview** to the
  AI endpoint configured in Settings, one frame at a time, waiting for each reply before
  taking the next. Frames are reduced to at most 768 px wide JPEGs before sending.
- **Where they go depends on your provider.** With a local Ollama server the frames never
  leave the machine. With an online provider (for example an OpenAI-compatible endpoint)
  the frames are uploaded to that service, exactly like the text you type into the chat.
  The Live vision card states which case applies under the switch.
- Only the newest frame is ever held, and nothing is written to disk unless you also use
  the Recording card.
- **What the model is asked to do with them:** comment on the setup and the picture —
  screen or mode, layout, HUD clutter, lighting, colour, blur, whether the image looks
  correct. It is explicitly instructed not to identify targets, enemies, or players and
  not to give aim, recoil, or positioning guidance. "Look now" asks the same question on
  demand.
- Turning the switch off stops the requests immediately; no frame is sent while it is off.

## In-Game Mode

- **Off at every launch.** It only turns on when you enable it in Settings, and it goes
  back to off the next time the app starts; the on/off state is not saved.
- When it is on, the values you type (game, mouse DPI, in-game sensitivity, and an
  optional measured 360° distance) are kept in your settings store so the card is
  pre-filled next time. They are small numbers you entered; Vantage does not read them
  from your mouse, your game, or your configuration files.
- Vantage never reads, hooks, or changes the game: no window access, no memory access,
  no input injection, and no capture of the game for this feature. The conversion is
  arithmetic on your numbers.
- While the mode is on, those numbers (and the resulting cm/360° and in-game suggestion)
  are included in the context sent to your configured AI endpoint — the same endpoint
  described under Network. With the mode off, nothing in-game is sent.
- If software you use while playing requires other applications to be closed, keep this
  mode off; the app's own Capture and crosshair features have the same consideration.

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
- Detection of enemy targets, enemies, or shooting from game footage, including through
  Live vision.
- Aim, recoil, or positioning coaching derived from what is on screen.
- Reading or changing game settings, memory, or input. In-Game Mode works only from
  numbers you type and never suggests that Vantage applies them.

Live vision is deliberately limited to the picture itself — layout, readability, lighting,
HUD clutter — and its prompt forbids target, enemy, and aim analysis. It is not a way to
have the app watch the game for you.

These were requested and then declined, because they would give a player an advantage
in live play and break the rules of games that use anti-cheat systems. They are not
built, and this policy does not describe them as features.

Recording is available and user-controlled: it starts when you press Record, stops when
you press Stop or close the app, and only outlives the session if you ask it to. It is a
file for your own review, not part of any in-play assistance.
