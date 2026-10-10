"""Census of EV/hybrid signals in OBDb and how many our engine can send.

Usage: python3 -I census.py <sigdir>
"""
import json, glob, os, re, sys, collections

EV_RE = re.compile(
    r"hybrid|\bhv\b|high.?voltage|traction|\bcells?\b|battery module|\bblock|state of charge|\bsoc\b|"
    r"state of health|\bsoh\b|charg|inverter|\bmotor|\bmg ?[12]\b|\bev\b|regen|\bpack\b|kwh|"
    r"plug|electric|isolation|\bbms\b|dc.?dc|converter|insulation|boost converter|onboard charger",
    re.I)
AUX_RE = re.compile(r"\baux|12 ?v\b|starter|engine battery|dark current|battery sensor|turbo|"
                    r"sleep time|cable disconnection|alternator|charge air|charge pressure|charge cooler|"
                    r"supercharg|intercool|torque converter|throttle|electric water pump|"
                    r"electric thermostat|fan motor|electric fan|control module|particulate|\bdpf\b|soot|"
                    r"service regeneration|status of full charge|wiper|window|mirror|\bseat|door|"
                    r"steering|brake|fuel|oil|transmission|clutch|\bgear|evap|purge|egr|glow|catalytic|"
                    r"waste ?gate|coolant pump|ecall|start/stop|traction control|regeneration|active regen|"
                    r"regen started|electric load|charge contactor|regenerative operation|regenerative request|"
                    r"normalized cumulative|service battery|storage battery|accumulator|cooling fan|"
                    r"motor status|since last charge|cylinder block|exhaust|particle|hydraulic|pedal|converter loss|"
                    r"battery charge level|battery total (?:dis)?charge", re.I)

# A vehicle qualifies only with several unmistakably high-voltage signals,
# so a gas car's stray "battery charge" (12 V) never makes it a hybrid.
STRONG_RE = re.compile(
    r"\bhv\b|high.?voltage|hybrid|traction battery|\bcells?\b|battery block|battery module|"
    r"state of charge|\bsoc\b|inverter|\bmg ?[12]\b|electric motor|drive motor|\bkwh\b|"
    r"charging|charger|insulation|isolation|\b48 ?v", re.I)


def is_strong(sig):
    return bool(STRONG_RE.search(sig.get("name", ""))) or sig.get("path", "").startswith("Battery.Cells")


def is_ev_signal(cmd, sig):
    name = sig.get("name", "")
    path = sig.get("path", "")
    if AUX_RE.search(name):
        return False
    if sig.get("suggestedMetric") in ("stateOfCharge", "stateOfHealth", "tractionBatteryVoltage",
                                       "tractionBatteryCurrent", "electricRange", "pluggedIn",
                                       "isCharging", "tractionBatteryCapacity",
                                       "tractionBatteryEfficiency"):
        return True
    if path.startswith("Battery") or path in ("Electrical", "Drivetrain", "Engine", "Stats", "Trips"):
        return bool(EV_RE.search(name)) or path.startswith("Battery.Cells") or path.startswith("Battery.Modules")
    return False


def blockers(cmd):
    b = []
    hdr = cmd.get("hdr")
    rax = cmd.get("rax")
    if not hdr:
        b.append("nohdr")
    if cmd.get("eax"):
        b.append("eax")
    if cmd.get("tst"):
        b.append("tst")
    if cmd.get("din") or cmd.get("dout"):
        b.append("uds")
    if cmd.get("pri") and cmd.get("pri") != "7DF":
        b.append("pri")
    if hdr and len(hdr) not in (3, 8):
        b.append("hdrwidth")
    return b


def main(sigdir):
    per_repo = collections.Counter()
    sendable_repo = collections.Counter()
    block_counts = collections.Counter()
    fmt_issue = collections.Counter()
    total = sendable = 0
    for f in sorted(glob.glob(os.path.join(sigdir, "*.json"))):
        repo = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for c in d.get("commands", []):
            sigs = [s for s in c.get("signals", []) if is_ev_signal(c, s)]
            if not sigs:
                continue
            b = blockers(c)
            for x in b:
                block_counts[x] += len(sigs)
            for s in sigs:
                total += 1
                per_repo[repo] += 1
                fm = s.get("fmt", {})
                bix, ln = fm.get("bix", 0), fm.get("len", 8)
                ok_fmt = (ln == 1) or (bix % 8 == 0 and ln in (8, 16, 24, 32))
                if "map" in fm:
                    fmt_issue["enum"] += 1
                if not ok_fmt:
                    fmt_issue[f"len{ln}"] += 1
                if not b and ok_fmt and "map" not in fm:
                    sendable += 1
                    sendable_repo[repo] += 1
    print("EV signals:", total, "sendable now:", sendable)
    print("blockers (signals):", block_counts.most_common())
    print("format issues:", fmt_issue.most_common())
    print("repos with EV signals:", len(per_repo), "sendable repos:", len(sendable_repo))
    for r, n in per_repo.most_common():
        print(f"  {r:40s} {n:5d} sendable {sendable_repo[r]:5d}")


if __name__ == "__main__":
    main(sys.argv[1])
