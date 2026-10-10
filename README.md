# Odo EV & hybrid PID presets

Extended OBD-II PID definitions for electric and hybrid vehicles, as used
by the Odo app's Custom PID presets. 99 vehicles, 12,929 signals: state of
charge, state of health, pack voltage/current/power, individual cell,
module and block voltages, battery temperatures, charging, and motor /
inverter data.

## Source and changes

Adapted from [OBDb](https://github.com/OBDb) (Clutch Engineering and
contributors), licensed CC BY-SA 4.0. Changes from the original:

- Kept only EV/hybrid signals from model repos (see `census.py`).
- Rewrote each signal's bit layout and scaling as a Torque-style formula
  (`a`, `b`, … for data bytes; `SIGNED`, `SIGNED16`, `BIT`).
- Expanded OBDb's short 29-bit headers to full CAN IDs and dropped
  commands needing extended addressing or a diagnostic session.
- Turned OBDb null bounds into plausibility ranges.
- Checked every formula against OBDb's recorded vehicle responses. Entries
  that reproduced them carry `"v"` (the number of matching responses);
  entries that disagreed were removed.

`convert.py` regenerates the file from a checkout of the OBDb repos.

## Format

`vehicles[]` → `{make, model, repo, pids[]}`. Each pid:
`n` name, `c` category, `m` mode, `p` PID/DID, `h` request header,
`r` receive address (only when not header + 8), `u` unit, `f` formula,
`lo`/`hi` plausible range, `i` poll interval (s), `y0`/`y1`/`ys` model
years, `k` OBDb suggested metric, `v` verified response count.

## License

CC BY-SA 4.0, same as OBDb. See `LICENSE`.
