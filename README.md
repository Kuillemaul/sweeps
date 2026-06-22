# Flemington Sweeps App - Save, Lock, Audit, Export Build

Desktop sweep app scaffold for running race sweeps.

## Screen size

The admin app now enforces a **1920 × 1080 minimum window size**. It is intended to run on a 1080p screen or larger. Smaller displays will be cramped and are not supported for the admin controls. The web display remains browser-based and can scale to TVs or second screens.

## Attendee test import

Two attendee import examples are included:

```text
data\import_examples\test_attendees.csv
data\import_examples\test_attendees_plain_list.txt
```

The CSV version includes the fields the app understands:

```text
attendee_id,name,active,cup_eligible,paid
```

Use the CSV if you want to test Active / Cup Eligible / Paid flags during import. Use the plain text version if you just want one attendee name per line.

## Run

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## New in this build

This build adds the first five admin/safety features:

1. **Save / Load Event**
   - Save the full sweep event to `.sweeps.json`.
   - Load it again later with races, attendees, allocations, paid flags, results, locked sweeps, payout settings and audit log intact.
   - Use the left-panel **Save Event** / **Load Event** buttons or the File menu.

2. **Lock Sweep**
   - Race screens now have **Lock current sweep** and **Unlock current sweep**.
   - Cup screen now has **Lock selected** and **Unlock selected**.
   - Locked sweeps cannot be regenerated or replaced by imports until unlocked.

3. **Audit Log**
   - New **Audit Log** page.
   - Tracks imports, draw generation, result entry, paid-status changes, locked/unlocked sweeps, payout setting changes, saves and exports.

4. **Export / Print Sheets**
   - New **Export / Print Sheets** button.
   - Exports:
     - `index.html`
     - printable race-card HTML files
     - `all_allocations.csv`
     - `amounts_owing.csv`
     - `payouts.csv`
     - `audit_log.csv`
   - Open `index.html` in a browser and print from there.

5. **Payout Settings**
   - New **Payout Settings** page.
   - Edit payout rules by pool total or sweep label.
   - Shows current generated sweep totals, payout totals and differences.

## Main screens

- **Import Data**: import attendee lists and race CSV files.
- **Race buttons**: appear only after race data has been imported or a race workbook has been loaded.
- **Money Owing**: shows what each attendee owes from generated allocations.
- **Attendees**: edit names and double-click Active, Cup Eligible, or Paid to toggle Yes/No.
- **Cup Sweep Generator**: draw the Cup $1, $2 and $5 sweeps separately using two animated spinner reels, with optional OpenAI live text-to-speech announcements.
- **Payout Settings**: edit the payout table.
- **Audit Log**: see what has happened in the event.
- **Clear Data**: clears all imported race data, attendees, generated sweeps and draw results after confirmation.

## Suggested workflow

1. Import attendees.
2. Import race CSVs.
3. Save the event.
4. Generate normal race sweeps.
5. Lock completed sweeps.
6. Generate/draw Cup sweeps separately.
7. Mark people as paid on the Attendees page.
8. Enter winning horse numbers.
9. Check Money Owing, Payout Settings and Payouts.
10. Export / Print Sheets.
11. Save the event again.

## OpenAI live text-to-speech

The Cup generator has:

- **Announce draw** checkbox
- **Voice engine** selector: `OpenAI live` or `Windows/offline fallback`
- **OpenAI voice** selector
- **Delivery** selector: `Plain`, `Race caller`, or `Big reveal`
- **Voice prompt** box for OpenAI style instructions
- **Test voice** button

The app reads your API key from:

```text
data\sweeps.env
```

The file should contain:

```text
OPENAI_API_KEY=your_real_key_here
```

A template is included at:

```text
data\sweeps.env.example
```

Do **not** put the API key directly inside `app.py`, and do **not** send your real `sweeps.env` file to anyone.

For now, OpenAI mode is intentionally live. Each Cup announcement calls the OpenAI `/v1/audio/speech` endpoint, receives a WAV file, repairs the WAV header if OpenAI returns a streaming-style header, saves a copy in `data\tts_cache`, and plays it. If the key is missing or the API call fails, the app falls back to the local Windows voice so the draw can continue.

Default model and voice settings:

```text
Model: gpt-4o-mini-tts
Default voice: marin
Response format: wav
Default prompt: You are voicing a Melbourne Cup office sweep draw. Use an upbeat race-day announcer delivery with more energy than normal narration. Sound clear, lively and slightly dramatic, but not cartoonish.
```

The Cup screen now includes an editable **Voice prompt** box and a **Delivery** selector. The prompt changes the delivery style, not the base voice identity. For a stronger audible difference, use **Race caller** or **Big reveal**; those modes rewrite the spoken announcement with pauses and race-day wording before it is sent to OpenAI. The **Reset prompt** button restores the default race-caller prompt.

## Cup spinner draw

Use **Cup Sweep Generator**:

1. Choose **Cup $1**, **Cup $2**, or **Cup $5**.
2. Click **Generate selected sweep**.
3. Leave **Announce draw** ticked if you want voice announcements.
4. Select **OpenAI live** as the voice engine when credits are available, or use Windows fallback.
5. Edit the **Voice prompt** if you want a different delivery style.
6. Click **Test voice** to confirm the API key and sound output.
6. Click **Draw next**.
7. The horse spinner stops first, then the attendee spinner stops.
8. The revealed result is added to the table and announced.
9. Lock the selected Cup sweep once done.

Each Cup sweep is generated and drawn separately. Regenerating one selected Cup sweep only replaces that selected sweep unless it is locked.

## Import race data

Use **Import Data > Import Race CSV(s)**.

The importer supports CSV files like the included example:

```text
data\import_examples\20260430-geraldton-r01.csv
```

Expected useful columns include:

- `Num`
- `Horse Name`
- `Barrier`
- `Weight` or `Weight Carried`
- `Jockey`
- `Trainer`
- `Finish Result (Updates after race)`
- `Form Guide Url` if available, used to infer the race name

The app tries to infer the race number from filenames like `r01`, `r02`, `race1`, etc. Use the Race Number Override box if the filename does not contain a useful race number.

Importing a race CSV replaces that race and clears generated allocations for that race only. Locked races are protected.

## Cup Special import

Use **Import Data > Import Cup Special Race CSV** for the special Cup race.

That import always loads the race as **Race 7 - Cup Special**, which keeps the $1, $2 and $5 Cup sweeps separate from normal 50c race sweeps.

## Current assumptions

- Normal races cost $0.50 per allocation.
- Race 7 uses the three Cup sweeps: $1, $2 and $5.
- Extra horses are paid allocations.
- Unpaid attendees are blocked from payout eligibility.
- The random seed box is for repeatable testing. Set it to Random for non-repeatable draws.


## OpenAI voice troubleshooting

OpenAI TTS reads the API key from `data\sweeps.env`:

```text
OPENAI_API_KEY=your_key_here
```

The Cup screen now shows the last OpenAI TTS status/error and saves generated audio files in `data\tts_cache`.

This build also fixes a Windows playback issue where OpenAI can return a valid streaming WAV with placeholder `0xFFFFFFFF` file-length values. Some players tolerate that, but Windows `winsound` and `System.Media.SoundPlayer` may refuse to play it. The app now rewrites the WAV header after download before playback.

If OpenAI says it generated audio but you hear nothing, click **Open audio folder** and double-click the latest `.wav` file.

- If the `.wav` file plays, the API is working and the issue is the app playback path.
- If the `.wav` file does not play, the generated file or Windows audio codec path is the issue.
- If the app shows an HTTP error, the issue is API key, billing, quota, or network.

## Random draws

The left panel has a **Random seed** control. Leave it on **Random** for live/event use so each regenerated sweep gets a fresh random allocation. Set a number only for testing when you deliberately want the same draw to be repeatable.


## Polished visuals and Web Display build

This build adds a cleaner dark-first visual theme, dashboard summary cards, clearer race status cards, and a read-only local web display.

### Web Display

Open the app, then go to **Web Display** in the left panel and click **Start web display**.

The app will show:

- **Local screen** — for the same computer
- **Network screen** — for other devices on the same network

Example:

```text
http://192.168.1.50:8765/
```

Open that address on another laptop, tablet, TV browser, or display PC connected to the same network.

Useful web pages:

```text
/          Dashboard
/cup       Big Cup draw display
/race/1    Race 1 easy-read allocations
/money     Money owing
/payouts   Payout winners
/attendees Attendee list
```

The web display is read-only. All imports, draws, locks, payout settings, and paid status changes still happen in the main desktop admin app.

If another device cannot connect:

1. Make sure both devices are on the same network.
2. Allow Python through Windows Firewall if prompted.
3. Check that the address uses the admin computer's local IP address.
4. Keep the Sweeps app open while displaying pages.

### Visual changes

- Dark mode is now the default.
- Left navigation has higher contrast and clearer spacing.
- Dashboard page added.
- Race pages now show summary cards for runners, active attendees, sweep tabs, and locked sweeps.
- Tables, tabs, buttons, and Cup spinner panels have been restyled.

## Cup web display behaviour

The Cup web display now only shows Cup draw rows after they have been revealed with **Draw next**. Generating a Cup sweep still creates the hidden allocation order in the admin app, but the public display does not expose unrevealed horses or attendees.

## Web display wheel and sound effects build

This build upgrades the `/cup` web display from a simple reveal screen into a browser-based big-screen draw display.

New web display features:

- Proper pokie-style vertical reel windows for **Horse** and **Attendee**.
- The horse reel lands first, then the attendee reel lands second.
- The web display uses live polling from the PySide admin app; the desktop app remains the backend/controller.
- Hidden/generated Cup allocations remain hidden. The web display only lists rows after they are revealed.
- Browser sound effects are generated locally with the Web Audio API:
  - reel tick sounds
  - horse stop clunk
  - attendee stop clunk
  - reveal/favourite/roughie chimes
- The `/cup` page has an **Enable sound** button. Click it once on the display screen before the draw starts; browsers usually block automatic audio until the page has been interacted with.
- Odds-based flair is shown when odds are present in the imported race CSV:
  - **Market favourite**
  - **Well fancied**
  - **Long odds roughie**
- The race CSV importer now looks for odds columns such as `Best Fixed Odds`, `Fixed Odds`, `Odds`, `Starting Price`, `SP`, `Price`, `Win Odds`, and `Market Price`.

Recommended setup for Cup day:

1. Start the web display from the desktop app.
2. Open `/cup` on the TV/display browser.
3. Click **Enable sound** on the display browser.
4. Use the PySide app as the admin controller and press **Draw next**.
5. The web display handles the wheel animation and sound effects while the admin app controls the actual fair draw order.

## Web wheel stop fix

This build fixes the Cup web display reels continuing to move after the draw has already landed in the admin app.

- The web display now stops/syncs as soon as the admin app reports the reveal as complete.
- Reels are not re-randomised on every web polling refresh after a result has landed.
- The revealed horse and attendee remain stable until the next **Draw next** action.

## Fix in staggered-stop build

The 3D `/cup3d` display now ignores the admin panel's early reveal state while its own big-screen wheel animation is still running. This prevents the web screen from snapping both reels to the result at the same time. The horse reel is allowed to finish first, then the attendee reel finishes second.


## Split reel-stop voice clips

The 3D Cup web display now asks the PySide backend for separate OpenAI audio clips when a draw starts:

- Horse reel stop: announces the horse number and horse name.
- Attendee reel stop: announces the attendee name.

The clips are cached in `data/tts_cache/web_stop_clips`, so repeated tests reuse the same generated audio instead of calling the API again. Use **Pre-cache web stop voices** on the Cup screen after generating a Cup sweep if you want to prepare the audio before the live draw.

Click **Enable sound** on `/cup3d` before drawing; browsers block audio until the display page has been interacted with.
