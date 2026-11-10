# Configuration and known cars

**English** · [Bahasa Indonesia](id/configuration.md) · [简体中文](zh-CN/configuration.md) · [Bahasa Melayu](ms/configuration.md)

The dashboard login page and `tools/setup.py` fill in most settings automatically. This reference covers optional fields and known-car defaults.

## `creds.json` reference
| key | required | what |
| --- | --- | --- |
| `email`, `password` | ✅ | your CarLinko login (plaintext over TLS; stored locally only) |
| `region` | | API region, default `sea` (Southeast Asia). **Login fails with the wrong one** — an account made in Europe/Middle East/Africa needs `emea` (reported on a J5 Noble in Israel, [#6](https://github.com/GodrezJr2/j5-ev-dashboard/issues/6)). If a correct password is refused, try another region before anything else |
| `vehicle_id`, `device_sn` | auto | your vehicle id + device serial — **`setup.py` fills these for you** |
| `vehicle` | auto | `{plate, model, vin}` — auto-detected; UI hides plate+VIN by default |
| `battery_kwh`, `wltp_kwh_100` | | usable pack size + WLTP reference. **The car never reports pack size**, so it comes from you or from the [known-cars table](#known-cars); an unrecognised model is asked at setup and labelled *assumed* in the UI until you set it. It scales every kWh, cost and efficiency figure |
| `currency` | | `{symbol, locale, code}` for non-IDR countries, e.g. `{"symbol":"R","locale":"en-ZA","code":"ZAR"}`. Default `Rp` / `id-ID` |
| `tariff`, `petrol_price`, `petrol_kml` | | your local charging tariff/kWh + petrol price/L + economy, in your currency |
| `tyre_unit` | | `psi` (default), `bar`, or `kpa` for the tyre display |
| `tpms_scale` | | raw tyre-byte → kPa scale. J5 is indirect (no real PSI); cars that send real pressure need this recalibrated (see below) |
| `car_image` | auto | override for the dashboard hero picture. Normally unnecessary — `setup.py` saves the render CarLinko hosts of *your* car (right model, right colour) and the server caches it at `/car-photo`. Set this to a filename in `web/` or a full URL only if you'd rather use your own photo |
| `specs` | | brochure figures for the **Specifications** card — `{label, source, performance:[[name,value,unit]], dimensions:[…]}`. Only the J5's are bundled; any other model hides the card rather than showing another car's numbers, so fill this in if you want it |
| `powertrain` | | `auto` (default), `bev` or `phev`. On `auto` the car is treated as a PHEV once it reports a fuel tank — a BEV never does, so this stays off unless it should be on |
| `chemistry` | | `lfp` (default) or `nmc` — picks the battery-care advice |
| `full_charge_days` | | how often to recommend a 100% charge. Default `7` for LFP, `90` for NMC |
| `gmaps_key` | | Google Maps key — enables trip planner + SPKLU map (else OSM fallback) |
| `dashboard_password` | | set it (login page → Advanced) to lock the dashboard behind a password — **do this if the URL is reachable from the internet** |

> **Not in Indonesia? `setup.py` now asks.** It offers presets for Indonesia, South Africa, the UK
> and the Eurozone (and lets you type any other currency), then asks for your charging tariff,
> petrol price and tyre unit — so every cost on the dashboard is in your money without hand-editing
> `creds.json`. Re-run `python setup.py` any time to change them; it keeps your existing values.
> The trip planner + charge map remain Indonesia-only (they use SPKLU/PLN data).
>
> Bundled prices are a *starting point* and go stale — Indonesian defaults are Pertamax at
> Rp16,250/L and PLN SPKLU at Rp2,540/kWh (Jul 2026). Set `petrol_price` / `tariff` to yours.

> **`tariff_idr` is the old name for `tariff`** — still read, so existing configs keep working, but
> prefer `tariff` now that costs aren't Indonesia-only.
>
> **Tyre pressure is now calibrated for you.** CarLinko publishes the per-model tyre formula
> (`appKpaFormula`, e.g. `data * 1.373`), so `setup.py` reads your car's own scale straight off the
> API — no hand-calibration. If you've already set `tpms_scale` yourself, setup keeps your value and
> just mentions what CarLinko says; delete the key to adopt theirs. Pick the display unit with
> `tyre_unit` (`psi`/`bar`/`kpa`).
>
> If the numbers still look wrong, the tyre card shows the **raw bytes** — read one wheel off your
> car's own screen and `tpms_scale = your_kPa / raw_byte`. Note that some cars (the J5 included)
> have *indirect* TPMS and report tyre **status only**, never a real pressure.

## Known cars

Settings other owners have confirmed. Please add yours via a
[compatibility report](https://github.com/GodrezJr2/j5-ev-dashboard/issues/new?template=compatibility.md).

These are **applied automatically** from your detected model — the table lives in
[`tools/known_cars.py`](../tools/known_cars.py), and anything you set in `creds.json` still wins.

| Car | `battery_kwh` | `tpms_scale` | `tyre_unit` | Notes |
| --- | --- | --- | --- | --- |
| Jaecoo J5 EV (ID) | `58.9` | n/a | `psi` | Reference car. *Indirect* TPMS — reports tyre **status only**, never a pressure |
| Chery Tiggo 8 PHEV 2025 (ZA) | `18.3` | `1.779` | `bar` | Direct TPMS (raw `136` = 2.42 bar). PHEV — fuel level, fuel consumption and fuel range all decoded ([#2](https://github.com/GodrezJr2/j5-ev-dashboard/issues/2)) |
| Chery Tiggo 7 PHEV (MY) | `18.3` | `1.779` | `psi` | Same pack and tyre scale as the Tiggo 8 PHEV, independently confirmed ([#3](https://github.com/GodrezJr2/j5-ev-dashboard/issues/3)). Malaysia badges it **`TIGGO 7 CSH`** — matched automatically |
| Omoda E5 2025 (UY) | `61` | — | — | LFP, WLTP 15.5 kWh/100 km — owner-confirmed ([#5](https://github.com/GodrezJr2/j5-ev-dashboard/issues/5)). Everything works incl. login, auto-detect and charging |

**If your car isn't listed**, nothing is invented on your behalf: `setup.py` asks for the pack size,
the battery card labels it *assumed* until you set it, and the spec card stays hidden rather than
showing another car's brochure figures.

**PHEVs.** Fuel tank % (byte 21), fuel consumption L/100 km (byte 53) and **fuel range**
(bytes 70–71) are decoded and shown next to the battery. Those bytes read `0` on every BEV frame, so
BEV owners see no fuel UI. `range_km` stays strictly **EV-only** and is labelled *EV range* on a
PHEV; the combined figure the car's own dash shows is **not transmitted** — it is exactly EV + fuel
(verified on three captures), so the app computes it and says so rather than passing it off as the
car's number. Because a PHEV also covers distance on petrol, kWh/100 km and savings-vs-petrol are
approximate on one.
