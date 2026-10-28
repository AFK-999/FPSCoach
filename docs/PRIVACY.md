# FPS Coach (Vantage) Privacy Policy

Last updated: 2026-10-28 (rev90)

This policy describes what FPS Coach stores, sends, and does not do. The same key statements appear on the app's Privacy page.

## Data kept on your machine

- **Training history** is saved to `.fpscoach_analytics.json` in the project folder. The Privacy page can delete it.
- **Preferences** are stored in your system settings store. This includes the requested calibration sampling rate and other UI choices.
- **Chat transcript** is kept only for the current session. Clearing the chat on the Privacy page removes it.
- New calibration results and recommendations are not written to the calibration-history file. Timestamped cursor samples are processed in memory and discarded at the end of each accepted target; the analyzer keeps only movement metrics and cursor-speed samples needed for the current session's recommendation. The app does not append new results to the file.

## Calibration data

- Calibration runs only after you start the explicit 12-target in-app task. The analyzer observes timestamped desktop cursor positions during that task; it does not read the game's raw-input stream or use screen frames for this analysis.
- Movement metrics and the provisional sensitivity curve are calculated locally from the captured cursor movement. The graph refreshes after each completed target, and its control-point count and speed positions follow the samples actually collected.
- Raw movement samples stay on this machine and are not sent to an AI provider. The calibration summary and up to 32 horizontal speed/gain points from the active calibrated or imported curve may be included in a text-coaching request, as described under **AI requests and network**.
- Calibration creates a recommendation for you to review. Export is explicit; the app does not apply a curve to a game or change game settings.

## Screen capture and recording

- The Capture tab's live preview processes captured frames in memory to display the preview and support explicitly enabled features such as Live vision. The calibration analyzer does not use those frames.
- **Recording (Capture tab, Recording card) is off unless you start it.** While it runs it records the primary display, either to a temporary file or, if you tick **Keep the recording**, to the folder you choose (`Videos/FPSCoach` by default).
  - Without **Keep**, the file is deleted when you press Stop or close the app.
  - With **Keep**, the file stays in that folder until you delete it, including across app restarts, and is named `fpscoach-recording-<date>-<time>.mp4`.
  - Recordings are never uploaded by the app. Anything you do with the file afterwards (send it, sync it, or open it elsewhere) is up to you.
- The recording analysis timer samples frame statistics (size and mean brightness) as a pipeline health check. It does not look for targets, shooting, or gameplay events.
- The capture window list reads window titles and process names only, so you can choose a window. It does not read or write any process's memory.

## AI requests and network

- AI requests are sent only to the endpoint you set in Settings. With local Ollama, the requests stay on that machine; an online provider receives the conversation and context you send to it.
- Text-coaching requests can include the conversation, numeric calibration summary, and up to 32 horizontal speed/gain points from the active curve. Raw cursor samples are not included. If In-Game Mode is on, the numbers you typed for that mode are also included; with it off, they are not sent.
- Voice recognition can run fully offline with a local model.

## Live vision (AI Coach)

- **Hidden and off until you ask for it.** The panel is not shown unless **Settings → Enable live vision** is on; that layout preference is remembered. Enabling it only shows the panel: **Watch live** still starts off at every launch and is not saved.
- While watching is on, Vantage samples the capture preview into short clips of downscaled frames (4 frames half a second apart by default) and sends one clip at a time to the configured AI endpoint, waiting for each reply before sampling the next. Frames are reduced to at most 768 px wide JPEGs. This feature records nothing to disk.
- With a local Ollama server the frames stay on the machine; with an online provider they are uploaded to that service. Only the frames for the clip being sent are held in memory, and they are discarded after the request.
- The model is asked to comment on the setup and picture (screen or mode, layout, HUD clutter, lighting, colour, blur, and readability). It is explicitly instructed not to identify targets, enemies, or players and not to give aim, recoil, or positioning guidance. **Look now** asks the same question on demand.
- Turning Watch live off stops requests; no frame is sent while it is off.

## In-Game Mode

- **Off at every launch.** It turns on only when you enable it in Settings for the current session; the on/off state is not saved.
- When on, the values you type (game, mouse DPI, in-game sensitivity, and optional measured 360° distance) are stored in your settings store so the card is pre-filled next time. Vantage does not read them from your mouse, game, or configuration files.
- Vantage never reads, hooks, or changes the game for this feature: there is no game-window access, memory access, input injection, or game capture. The conversion is arithmetic on your numbers.
- While the mode is on, those numbers and the resulting conversion/suggestion are included in context sent to your configured AI endpoint. With the mode off, nothing in-game is sent.
- If software you use while playing requires other applications to be closed, keep this mode off; the app's own Capture and crosshair features have the same consideration.

## Crosshair overlay

- The overlay is **off by default**. When it is on, or while you edit its position, it draws a crosshair on your primary screen only.
- The active crosshair overlay does not interact with or inject into any game process. It does not read game frames, hook input, or read or write game memory.
- When locked, it ignores the mouse so clicks reach the game. Its position is saved on your machine.

## Fair-play boundary

FPS Coach is a coaching and calibration tool, not an aiming tool. It does not offer gameplay-based feedback drawn over a game, target/enemy/shooting detection, aim/recoil/positioning advice derived from game footage, game-memory access, input hooks or injection, or automatic game-setting changes. Live vision is deliberately limited to the picture's setup and readability; its prompt forbids target, enemy, and aim analysis. Recording is user-controlled and intended for the user's own review, not in-play assistance.
