# Accuracy notes

**English** · [Bahasa Indonesia](id/accuracy.md) · [简体中文](zh-CN/accuracy.md) · [Bahasa Melayu](ms/accuracy.md)

## Receipt comparisons

The charge analytics are calibrated against the owner's real PLN Mobile receipts:

| Session            | Dashboard            | Receipt              | Match   |
| ------------------ | -------------------- | -------------------- | ------- |
| 58 → 100 %         | 28.9 kWh / Rp 73,491 | 28.94 kWh / Rp 73,521 | 99.9 % |
| 35 → 80 %          | 29.1 kWh / Rp 73,981 | 29.23 kWh / Rp 74,273 | 99.6 % |

DC charge efficiency is modelled as SoC-dependent (charging to 100 % loses more than to 80 %),
calibrated to two receipts. Usable pack ≈ 58.9 kWh.

The charge planner predicts what you'll actually pay at the meter, checked against a real
PLN Mobile SPKLU receipt:

| The app's charge planner | The real PLN Mobile receipt |
| :---: | :---: |
| ![Charge planner estimate](screenshots/accuracy-app.png) | ![PLN Mobile SPKLU receipt](screenshots/accuracy-receipt.png) |

The app estimates **58.2 kWh** to buy at the meter at **Rp 2,540/kWh**; the receipt shows
**57.34 kWh** actually delivered at the same **Rp 2,540/kWh** all-in tariff — the per-kWh
price is exact and the volume lands within ~1.5 % (the receipt session stopped a little short
of a full charge). The refund maths line up too: bought Rp 152,448, used Rp 145,694.
