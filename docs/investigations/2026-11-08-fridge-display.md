# Fridge meal display (larder-cyd)

- **Date:** 2026-11-08
- **Investigator:** Claude Code (Opus 5.5) for Deej
- **Status:** Firmware builds for all four boards and the endpoint is tested. Not yet flashed or tried on hardware.

## 1. What was asked

Deej wants a physical display for the fridge that shows the planned meals for each night over the next
fortnight or month, so the household can see the plan. He asked for it to use the CYD template from
the temperature sensor project (`cyd-air-monitor`), to live in a new public repo called `larder-cyd`, and
to come with a full plan, BOM and pinouts before the code.

## 2. What was checked

- The Larder API (`routes/api.py`). `/api/v1/plan` returns the whole saved plan range with servings and
  flags. It isn't anchored to today and is larger than the device needs.
- `cyd-air-monitor`, the newest CYD reference. It has four PlatformIO envs (JC4827W543R 4.3" 480x272
  ESP32-S3; ESP32-2432S028R, JC2432W328R and JC2432W328C 2.8" 320x240), and the modules TemplateCode,
  MainInterface, PeriodicScheduler, SettingsStore (NVS), WiFiConnectionManager (setup hotspot) and
  WebServerManager (local web page).
- The TLS chain for both Larder hosts. groceries.deejpotter.com and groceries-staging both chain
  Cloudflare WE1 to GTS Root R4, cross-signed by GlobalSign Root CA.
- Whether a device can reach the API. `GET /api/v1/plan` without a key returns the app's own 401 JSON
  on both hosts, so Cloudflare Access is not in front of `/api`.
- Hardware facts and AUD prices, gathered by a research subagent (sources are linked in the larder-cyd
  README).

## 3. What the evidence showed

- A small endpoint built for the device is better than parsing `/plan` on an ESP32. The server decides
  every label, so the firmware stays dumb.
- LVGL's built-in Montserrat fonts have no "…" glyph, so names are cut with "...".
- GlobalSign Root CA is no longer in Mozilla's store (certifi). Trusting GTS Root R4 directly works:
  `openssl s_client -CAfile <embedded roots>` returns `Verify return code: 0 (ok)` for both hosts.
- JC4827W543R facts: holes are M3 (R1.6) at 112.6 x 62.1 mm, the module is 120 x 70.2 mm, the active
  area is 95.04 x 53.86 mm, and it draws about 260 mA at 5 V. P4 is GND, 3.3V, IO17, IO18. There's no
  LDR. P5 looks like a copy of P4 (unverified).
- 2432S028R facts: the LDR is on IO34. CN1 is GND, IO22, IO27, 3V3, and IO27 is unconnected on some
  revisions.
- Battery power isn't practical: about 7 hours from 2000 mAh.

## 4. What was changed

Larder (`feat/fridge-display`):

- `services/display_board.py`: `build_board()`, `board_info()`, `clamp_days()`, `meal_note()`.
- `GET /api/v1/display` in `routes/api.py`.
- `tests/test_display_board.py`: 7 tests.
- An `AGENTS.md` feature note.

New repo `Deejpotter/larder-cyd`, copied from cyd-air-monitor without its git history or docs-site:

- `LarderClient` makes the HTTPS call with the embedded root CAs and parses it with ArduinoJson 7. It
  refreshes every 15 minutes, retries every minute after an error, and keeps the last good board.
- `MainInterface` shows a fortnight grid on 480px boards and a day list otherwise, with today in
  magenta and a payday and shopping footer.
- `Backlight` does PWM, dims at night by NTP time (Australia/Sydney), and wakes on touch or an
  optional PIR.
- `BoardConfig.h` holds per-board pins chosen by `MODEL_*`. The PIR is off by default.
- `SettingsStore` stores the Larder settings. The web page gets a Larder card (URL, key, days, meals,
  view, dimming) and the `/api/larder` routes.
- Renames: the hotspot is `Larder-Setup`, mDNS is `larder.local`, and the NVS namespace is `larder`.
- The sensor code (DHT/BME680) and its libraries are removed.

## 5. What was deliberately not changed

- No login on the device's web page. The template has none, and adding one means a password to set
  up on a fridge screen. Instead, changing the URL clears the saved key, so someone on the LAN can't
  redirect the key to their own server.
- No scoped or read-only API keys in Larder. The display key can read everything the API exposes.
  It's documented as "give it its own key so it can be revoked".
- No enclosure. No published case fits the JC4827, so it's listed as the next step (a CadQuery model
  via fab-cad).
- Not using the 2.8" board's LDR, and no tap-for-detail view. Both are listed as to-dos.
- `cyd-air-monitor` was not modified.

## 6. Verification

- `python -m pytest`: 1113 passed.
- `pio run` succeeds for jc4827w543r (RAM 38.6%, flash 43.0%), 2432s028r (RAM 35.5%, flash 68.2%),
  jc2432w328r and jc2432w328c.
- The embedded roots verify both live hosts with OpenSSL.
- **Not verified:** running on real hardware (display layout, touch, TLS through mbedTLS, heap during
  TLS on the non-PSRAM 2.8" board), and the endpoint on staging (it ships with this PR).

## 7. Open items

- Flash a JC4827W543R, check the layout and TLS, and tune the fonts.
- A 3D-printed magnetic case.
- Merge this PR to dev, confirm `/api/v1/display` on staging, then promote to main.

## 8. Commands to reproduce

```powershell
cd ~\repos\groceries-spend-visualiser; python -m pytest tests/test_display_board.py
cd ~\repos\larder-cyd; python -m platformio run -e jc4827w543r -e 2432s028r
python scripts/gen_root_certs.py
```
