# Features, demo and screenshots

The stock CarLinko app hides most of what the car reports: tyres show only "normal/abnormal", and there are no trip totals, charge-cost history or road-trip planner. This dashboard reads **your own account** and presents data the car already sends to its cloud.

## Features

- **Live status**: battery %, range, odometer, 12 V, and online / parked / charging / driving state, from the realtime WebSocket, cached in SQLite.
- **Charging**: charge sessions detected automatically (kWh into the pack, kWh billed at the meter, cost), a charge-curve chart, weekly and monthly counts, and a "charge to X %" planner. Regen blips are filtered out of the history.
- **Efficiency and trips**: per-trip and rolling kWh/100 km, lifetime kWh / cost / km, and savings versus petrol at your own petrol price.
- **Long-trip planner**: set a start and finish and get on-route charge stops sized to arrive with a safety margin, with connector type, kW and live availability from Google. Indonesia only (SPKLU/PLN data).
- **Charger map**: pan an interactive SPKLU map and tap a charger for connectors, availability and directions (data from Google Places). Indonesia only.
- **Remote control (beta)**: lock/unlock, A/C, windows, sunroof, tailgate, find-car and stop charging. Only *stop charging* is a confirmed opcode; every tap is a real command to a real car. Cloud only, no Bluetooth. See [control notes](control-opcodes.md).
- **Battery care, service countdown, tyre view, privacy toggles, dark mode, EN/ID interface.**
- **Home Assistant**: REST sensors plus battery-low and charge-done notifications. See [Home Assistant](HOMEASSISTANT.md). A native read-only alternative is [ha-carlinko](https://github.com/jebentancour/ha-carlinko).

Product rationale is in [PRODUCT.md](../PRODUCT.md), the visual system in [DESIGN.md](../DESIGN.md).

## Which cars work

Confirmed on four cars: Jaecoo J5 EV (Indonesia), Chery Tiggo 8 PHEV (South Africa), Chery Tiggo 7 PHEV (Malaysia) and Omoda E5 (Uruguay). Telemetry byte offsets were identical on all of them, so the layout appears to come from the CarLinko platform, not the model. Per-model values (tyre scale, powertrain, car photo) are read from the API at setup. Settings per car are in [Configuration → Known cars](configuration.md#known-cars).

Caveat: Jaecoo, Omoda, Exeed and Chery are all Chery Group brands, and CarLinko is Chery Group's app. Four cars is a strong signal, not proof; unmapped bytes may differ, and no pure-ICE car has been tried. If your car isn't listed, please file a [compatibility report](https://github.com/GodrezJr2/j5-ev-dashboard/issues/new?template=compatibility.md).

## Demo

<p align="center"><img src="media/walkthrough.gif" width="640" alt="Walkthrough of the v2 interface: home, energy, distance calendar, 3D doors view, charging and the charge calculator"></p>

The built-in demo mode runs on sample data with no account and no car:

```bash
cd tools && python server.py --demo      # then open http://localhost:8088
# or: docker compose run --rm -p 8088:8088 web python server.py --demo 8088
```

A *Demo mode* banner marks the data as fake. A recording against a real Jaecoo J5 EV (Classic interface, plate and VIN masked) is [on GitHub](https://github.com/user-attachments/assets/f17d167d-cbf9-4eb0-92b4-44361f6da6a5).

## Screenshots

**v2 interface** (`/v2.html`, or set it as the default under Settings). Light and dark themes are built in; these are the light ones. Demo data; plate and VIN are masked by default.

| Home | Energy | Distance |
| :---: | :---: | :---: |
| <img src="screenshots/v2-home-light.jpg" width="220" alt="Home: range, battery, climate, doors and tyres"> | <img src="screenshots/v2-energy-light.jpg" width="220" alt="Energy: kWh/100 km per day beside the head-unit figure"> | <img src="screenshots/v2-dist-light.jpg" width="220" alt="Distance: calendar of km, energy and charging cost per day"> |
| **Doors & windows** | **Charging** | **Charge calculator** |
| <img src="screenshots/v2-doors-light.jpg" width="220" alt="Doors and windows on a 3D car, with remote controls"> | <img src="screenshots/v2-chg-light.jpg" width="220" alt="Charging: last session curve, kWh in and cost"> | <img src="screenshots/v2-plan-light.jpg" width="220" alt="Charge calculator: time, kWh and cost to a target %"> |

The **Classic interface** (the default at `/`) adds the long-trip planner and the charger map:

| Trip planner | Charger map |
| :---: | :---: |
| <img src="screenshots/trip-planner.png" width="260" alt="Long-trip planner with on-route charge stops"> | <img src="screenshots/spklu-map.png" width="260" alt="SPKLU charger map"> |
