"""Convert OBDb signalsets into Odo EV/hybrid custom-PID presets.

Usage: venv/bin/python -I convert.py <repos_dir> <out_json> <report_txt>

Every emitted preset is expressible in our CustomPIDFormula grammar
(a..z/aa.. bytes, + - * / ( ), SIGNED, SIGNED16, BIT) and sendable by our
custom-PID engine (ATSH header, optional ATCRA receive address). Each
formula is checked against OBDb's recorded test fixtures: a preset whose
fixtures disagree with our formula is dropped, one with agreeing fixtures
is marked verified, one with no fixtures ships unverified.
"""
import glob
import json
import math
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from census import is_ev_signal, is_strong  # noqa: E402

MULTIWORD_MAKES = ["Mercedes-Benz", "Land-Rover", "Alfa-Romeo", "Aston-Martin", "Rolls-Royce"]
MAKE_DISPLAY = {"FIAT": "Fiat", "Seat": "SEAT", "Smart": "smart", "VW": "Volkswagen",
                "DongFeng": "Dongfeng", "Land-Rover": "Land Rover", "Alfa-Romeo": "Alfa Romeo",
                "Aston-Martin": "Aston Martin", "Rolls-Royce": "Rolls-Royce",
                "Mercedes-Benz": "Mercedes-Benz", "VauxhallOpel": "Vauxhall/Opel",
                "Landrover": "Land Rover"}

UNITS = {
    "volts": "V", "millivolts": "mV", "celsius": "°C", "fahrenheit": "°F", "percent": "%",
    "scalar": "", "amps": "A", "milliamps": "mA", "rpm": "rpm", "newtonMeters": "Nm",
    "kilometers": "km", "miles": "mi", "ampereHours": "Ah", "kilowattHours": "kWh",
    "kilowatts": "kW", "watts": "W", "minutes": "min", "seconds": "s", "hours": "h",
    "milliseconds": "ms", "milliohms": "mΩ", "kiloohms": "kΩ", "ohms": "Ω", "hertz": "Hz",
    "psi": "psi", "kilopascal": "kPa", "millimeters": "mm", "degrees": "°",
    "kilowattHoursPer100Miles": "kWh/100mi", "kilometersPerHour": "km/h",
}


def category(path, name):
    n = name.lower()
    if path.startswith("Battery.Cells"):
        return "Cells"
    if path.startswith("Battery.Modules"):
        return "Modules"
    if path.startswith("Battery.Charging") or "charg" in n or "plug" in n:
        return "Charging"
    if path.startswith("Battery"):
        return "Battery"
    if re.search(r"motor|inverter|mg ?[12]|converter|dc.?dc", n):
        return "Motor & Inverter"
    return "Energy"


# OBDb repo names use "-" for spaces, so real hyphens are lost. Restore
# them for the names drivers know.
MODEL_DISPLAY = {
    "CR V": "CR-V", "CR V Hybrid": "CR-V Hybrid", "CR Z": "CR-Z", "C HR": "C-HR",
    "e tron": "e-tron", "Q4 e tron": "Q4 e-tron", "e Golf": "e-Golf", "e Up": "e-Up",
    "E PACE": "E-PACE", "I PACE": "I-PACE", "Mustang Mach E": "Mustang Mach-E",
    "Corsa e": "Corsa-e", "Citigo e iV": "Citigo e-iV", "eC4 X": "ë-C4 X",
    "IONIQ PlugIn Hybrid": "IONIQ Plug-in Hybrid",
}


def model_display(model):
    if model in MODEL_DISPLAY:
        return MODEL_DISPLAY[model]
    # "F 150 Lightning" -> "F-150 Lightning", "CX 50" -> "CX-50"
    return re.sub(r"\b([A-Z]{1,3}) (\d{2,4})\b", r"\1-\2", model)


def split_repo(repo):
    for mw in MULTIWORD_MAKES:
        if repo.startswith(mw + "-"):
            return MAKE_DISPLAY.get(mw, mw), model_display(repo[len(mw) + 1:].replace("-", " "))
    make, _, model = repo.partition("-")
    return MAKE_DISPLAY.get(make, make), model_display(model.replace("-", " "))


def letter(i):
    if i < 26:
        return chr(97 + i)
    i -= 26
    return chr(97 + i // 26) + chr(97 + i % 26)


def num(x):
    """Shortest exact decimal for a JSON number."""
    if isinstance(x, int) or (isinstance(x, float) and x.is_integer()):
        return str(int(x))
    return repr(float(x))


def raw_expr(fmt):
    """Integer raw value as a formula, or None when the grammar can't say it."""
    bix = fmt.get("bix", 0)
    ln = fmt["len"]
    if ln == 1:
        return f"BIT({letter(bix // 8)}, {7 - bix % 8})"
    if bix % 8 or ln not in (8, 16, 24, 32):
        return None
    nbytes = ln // 8
    idx = [bix // 8 + k for k in range(nbytes)]
    if fmt.get("blsb"):
        idx.reverse()
    if idx[-1] > 26 * 27 - 1:
        return None
    weights = [256 ** (nbytes - 1 - k) for k in range(nbytes)]
    terms = [letter(i) if w == 1 else f"{letter(i)}*{w}" for i, w in zip(idx, weights)]
    expr = terms[0] if len(terms) == 1 else "(" + "+".join(terms) + ")"
    if fmt.get("sign"):
        if ln == 8:
            expr = f"SIGNED({expr})"
        elif ln == 16:
            expr = f"SIGNED16({expr})"
        else:
            return None
    return expr


def formula(fmt):
    expr = raw_expr(fmt)
    if expr is None:
        return None
    mul, div, add = fmt.get("mul", 1), fmt.get("div", 1), fmt.get("add", 0)
    if div == 0:
        return None
    if mul != 1:
        expr = f"{expr}*{num(mul)}"
    if div != 1:
        expr = f"{expr}/{num(div)}"
    if add:
        expr = f"{expr}+{num(add)}" if add > 0 else f"{expr}-{num(-add)}"
    return expr


def eval_formula(f, data):
    """Python twin of CustomPIDFormula for fixture checks."""
    def repl(m):
        tok = m.group(0)
        if tok in ("BIT", "SIGNED", "SIGNED16"):
            return tok
        i = ord(tok[0]) - 97 if len(tok) == 1 else 26 + (ord(tok[0]) - 97) * 26 + ord(tok[1]) - 97
        if i >= len(data):
            raise IndexError
        return str(data[i])

    def SIGNED(x):
        x = int(round(x)) % 256
        return x - 256 if x >= 128 else x

    def SIGNED16(x):
        x = int(round(x)) % 65536
        return x - 65536 if x >= 32768 else x

    def BIT(x, n):
        return (int(round(x)) >> int(n)) & 1

    expr = re.sub(r"\b(BIT|SIGNED16|SIGNED|[a-z]{1,2})\b", repl, f)
    return float(eval(expr, {"__builtins__": {}}, {"BIT": BIT, "SIGNED": SIGNED, "SIGNED16": SIGNED16}))


def bounds(fmt):
    """Plausibility window. OBDb clamps to [min, max] and nulls at
    nullmin/nullmax; our engine suppresses outside [lo, hi], so a null
    bound becomes the window edge nudged half a step inward."""
    lo, hi = fmt.get("min", 0), fmt["max"]
    step = abs(fmt.get("mul", 1) / fmt.get("div", 1)) or 1
    if "nullmin" in fmt:
        lo = max(lo, fmt["nullmin"] + step / 2)
    if "nullmax" in fmt:
        hi = min(hi, fmt["nullmax"] - step / 2)
    return lo, hi


def addressing(cmd):
    """(header, receiveAddress) for our engine, or None when unsupported."""
    hdr = cmd.get("hdr")
    if not hdr or cmd.get("eax") or cmd.get("din") or cmd.get("dout") or cmd.get("proto") in ("9141-2", "14230"):
        return None
    rax = cmd.get("rax")
    if len(hdr) == 3:
        if cmd.get("tst") or cmd.get("pri"):
            return None
        h = int(hdr, 16)
        if rax is None or int(rax, 16) == h + 8:
            return hdr.upper(), None
        if len(rax) != 3:
            return None
        return hdr.upper(), rax.upper()
    if len(hdr) == 4:
        pri = int(cmd.get("pri", "18"), 16) & 0x1F
        tst = cmd.get("tst", "F1").upper()
        header = f"{pri:02X}{hdr.upper()}{tst}"
        target = hdr[2:].upper()
        standard = f"{pri:02X}DA{tst}{target}" if hdr.upper().startswith("DA") else None
        if rax is None:
            reply = standard
        else:
            r = int(rax, 16)
            if r > 0xFFFFFF:
                reply = f"{r & 0x1FFFFFFF:08X}"
            elif r <= 0xFF:
                reply = f"{pri:02X}DAF1{r:02X}"
            else:
                reply = f"{pri:02X}{r & 0xFFFFFF:06X}"
        if reply is None:
            return None
        return header, (None if reply == standard else reply)
    return None


def parse_frames(response):
    """Reassemble ISO-TP payloads from a fixture response, per CAN id."""
    lines = [l.strip().upper() for l in str(response).splitlines() if l.strip()]
    msgs = {}
    order = []
    for line in lines:
        if not re.fullmatch(r"[0-9A-F]+", line):
            continue
        for idlen in (3, 8):
            if len(line) <= idlen + 2:
                continue
            can_id, body = line[:idlen], line[idlen:]
            if len(body) % 2:
                continue
            data = bytes.fromhex(body)
            pci = data[0] >> 4
            if pci == 0 and 0 < (data[0] & 0xF) <= len(data) - 1:
                msgs[can_id] = {"buf": bytearray(data[1:1 + (data[0] & 0xF)]), "need": 0}
                order.append(can_id)
                break
            if pci == 1 and len(data) >= 2:
                total = ((data[0] & 0xF) << 8) | data[1]
                msgs[can_id] = {"buf": bytearray(data[2:]), "need": total}
                order.append(can_id)
                break
            if pci == 2 and can_id in msgs:
                msgs[can_id]["buf"] += data[1:]
                break
        # lines matching neither width (e.g. request echo) are ignored
    out = []
    for cid in dict.fromkeys(order):
        m = msgs[cid]
        buf = bytes(m["buf"][: m["need"]] if m["need"] else m["buf"])
        out.append((cid, buf))
    return out


def load_fixtures(repo_dir):
    """command_id -> list of (response, expected_values)."""
    fx = {}
    for f in glob.glob(os.path.join(repo_dir, "tests", "test_cases", "*", "commands", "*.yaml")):
        try:
            d = yaml.safe_load(open(f))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        cid = str(d.get("command_id", ""))
        year_dir = os.path.basename(os.path.dirname(os.path.dirname(f)))
        year = int(year_dir) if year_dir.isdigit() else None
        for tc in d.get("test_cases") or []:
            if isinstance(tc, dict) and "response" in tc:
                fx.setdefault(cid, []).append((year, tc["response"], tc.get("expected_values") or {}))
    return fx


def fixture_key(cmd):
    """Approximate OBDb command_id prefix: hdr.rax.cmd"""
    service, did = next(iter(cmd["cmd"].items()))
    parts = [cmd["hdr"]]
    if cmd.get("rax"):
        parts.append(cmd["rax"])
    parts.append(f"{service}{did}")
    return ".".join(parts)


def close(got, want):
    """Fixtures store values rounded to a few decimals; compare at the
    fixture's own precision."""
    text = repr(float(want))
    decimals = 0 if "e" in text else len(text.split(".")[1].rstrip("0"))
    return abs(got - want) <= 0.5 * 10 ** -decimals + 1e-9 or math.isclose(got, want, rel_tol=1e-6)


def year_matches(filt, year):
    if not filt or year is None:
        return True
    if year in (filt.get("years") or []):
        return True
    lo, hi = filt.get("from"), filt.get("to")
    if lo is None and hi is None:
        return False
    return (lo is None or year >= lo) and (hi is None or year <= hi)


def check(sig, f, cmd, fixtures):
    """(matches, mismatches) of our formula vs recorded expected values."""
    service, did = next(iter(cmd["cmd"].items()))
    echo = bytes.fromhex(f"{int(service, 16) + 0x40:02X}{did}")
    key = fixture_key(cmd)
    addr = addressing(cmd)
    reply_id = None
    if addr:
        h, r = addr
        if r:
            reply_id = r
        elif len(h) == 3:
            reply_id = f"{int(h, 16) + 8:03X}"
        else:
            reply_id = f"{h[:2]}DA{h[6:]}{h[4:6]}"
    ok = bad = 0
    for cid, cases in fixtures.items():
        if cid.split("|")[0] != key:
            continue
        for year, response, expected in cases:
            if not year_matches(cmd.get("filter"), year):
                continue
            if sig["id"] not in expected:
                continue
            want = expected[sig["id"]]
            if not isinstance(want, (int, float)):
                continue
            frames = [(cid_, p) for cid_, p in parse_frames(response) if p.startswith(echo)]
            preferred = [x for x in frames if x[0] == reply_id]
            for _, payload in (preferred or frames)[:1]:
                data = payload[len(echo):]
                try:
                    got = eval_formula(f, data)
                except (IndexError, ZeroDivisionError):
                    bad += 1
                    break
                lo, hi = sig["fmt"].get("min", 0), sig["fmt"]["max"]
                clamped = max(lo, min(got, hi)) if hi > lo else got
                if close(clamped, want):
                    ok += 1
                else:
                    bad += 1
                break
    return ok, bad


def years(filt):
    if not filt:
        return None, None, None
    return filt.get("from"), filt.get("to"), filt.get("years")


def main(repos_dir, out_path, report_path):
    vehicles = []
    stats = {"signals": 0, "emitted": 0, "verified": 0, "dropped_mismatch": 0,
             "skip_addressing": 0, "skip_format": 0, "skip_unit": 0}
    mismatches = []
    for repo_dir in sorted(glob.glob(os.path.join(repos_dir, "*-*"))):
        repo = os.path.basename(repo_dir)
        if "obdb.community" in repo or repo.startswith("vscode") or repo in MULTIWORD_MAKES:
            continue
        sig_path = os.path.join(repo_dir, "signalsets", "v3", "default.json")
        if not os.path.exists(sig_path):
            continue
        try:
            data = json.load(open(sig_path))
        except Exception:
            continue
        fixtures = None
        make, model = split_repo(repo)
        pids = []
        seen = set()
        for cmd in data.get("commands", []):
            if not cmd.get("cmd"):
                continue
            service, did = next(iter(cmd["cmd"].items()))
            if service not in ("21", "22"):
                continue
            for sig in cmd.get("signals", []):
                if not is_ev_signal(cmd, sig):
                    continue
                stats["signals"] += 1
                fmt = sig.get("fmt", {})
                addr = addressing(cmd)
                if addr is None:
                    stats["skip_addressing"] += 1
                    continue
                unit = fmt.get("unit")
                if "map" in fmt or unit not in UNITS:
                    stats["skip_unit"] += 1
                    continue
                f = formula(fmt)
                if f is None:
                    stats["skip_format"] += 1
                    continue
                if fixtures is None:
                    fixtures = load_fixtures(repo_dir)
                ok, bad = check(sig, f, cmd, fixtures)
                if bad:
                    stats["dropped_mismatch"] += 1
                    mismatches.append(f"{repo} {sig['id']} {f} ok={ok} bad={bad}")
                    continue
                lo, hi = bounds(fmt)
                if lo >= hi:
                    # A null bound left no valid window; every read would be suppressed.
                    stats["skip_format"] += 1
                    continue
                y0, y1, ylist = years(cmd.get("filter"))
                key = (addr, service, did, f, y0, y1, json.dumps(ylist))
                if key in seen:
                    continue
                seen.add(key)
                entry = {
                    "id": sig["id"], "n": sig.get("name", sig["id"]),
                    "c": category(sig.get("path", ""), sig.get("name", "")),
                    "m": service, "p": did.upper(), "h": addr[0], "u": UNITS[unit],
                    "f": f, "lo": lo, "hi": hi,
                    "i": max(1, min(int(cmd.get("freq", 5)), 60)),
                }
                if addr[1]:
                    entry["r"] = addr[1]
                if y0:
                    entry["y0"] = y0
                if y1:
                    entry["y1"] = y1
                if ylist:
                    entry["ys"] = ylist
                if sig.get("suggestedMetric"):
                    entry["k"] = sig["suggestedMetric"]
                if ok:
                    entry["v"] = ok
                    stats["verified"] += 1
                stats["emitted"] += 1
                entry["_strong"] = is_strong(sig)
                pids.append(entry)
        strong = sum(1 for p in pids if p.pop("_strong"))
        if strong < 3:
            stats["vehicle_not_ev"] = stats.get("vehicle_not_ev", 0) + 1
            stats["emitted"] -= len(pids)
            stats["verified"] -= sum(1 for p in pids if "v" in p)
            pids = []
        if pids:
            vehicles.append({"make": make, "model": model, "repo": repo, "pids": pids})
    out = {
        "source": "OBDb (https://github.com/OBDb), licensed CC BY-SA 4.0. Converted for Odo.",
        "license": "CC-BY-SA-4.0",
        "vehicles": vehicles,
    }
    with open(out_path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    with open(report_path, "w") as fh:
        fh.write(json.dumps(stats, indent=1) + "\n")
        fh.write(f"vehicles {len(vehicles)}\n")
        for v in vehicles:
            nv = sum(1 for p in v["pids"] if "v" in p)
            fh.write(f"{v['make']:16s} {v['model']:30s} {len(v['pids']):5d} verified {nv:5d}\n")
        fh.write("\nMISMATCHES\n" + "\n".join(mismatches) + "\n")
    print(json.dumps(stats), "vehicles", len(vehicles))


if __name__ == "__main__":
    main(*sys.argv[1:4])
