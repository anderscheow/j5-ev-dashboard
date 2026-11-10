# Catatan akurasi

[English](../accuracy.md) · **Bahasa Indonesia** · [简体中文](../zh-CN/accuracy.md) · [Bahasa Melayu](../ms/accuracy.md)

## Perbandingan dengan struk

Analitik pengisian dikalibrasi terhadap struk PLN Mobile asli milik pemilik:

| Sesi               | Dashboard             | Struk                 | Cocok   |
| ------------------ | --------------------- | --------------------- | ------- |
| 58 → 100 %         | 28,9 kWh / Rp 73.491  | 28,94 kWh / Rp 73.521 | 99,9 % |
| 35 → 80 %          | 29,1 kWh / Rp 73.981  | 29,23 kWh / Rp 74.273 | 99,6 % |

Efisiensi pengisian DC dimodelkan bergantung pada SoC (mengisi sampai 100 % lebih boros daripada sampai 80 %), dikalibrasi dari dua struk. Pack yang terpakai ≈ 58,9 kWh.

Perencana pengisian memperkirakan yang benar-benar kamu bayar di meteran, dicek terhadap struk SPKLU PLN Mobile asli:

| Perencana pengisian di app | Struk PLN Mobile asli |
| :---: | :---: |
| ![Estimasi perencana pengisian](../screenshots/accuracy-app.png) | ![Struk SPKLU PLN Mobile](../screenshots/accuracy-receipt.png) |

App memperkirakan **58,2 kWh** yang dibeli di meteran dengan tarif **Rp 2.540/kWh**; struk menunjukkan **57,34 kWh** yang benar-benar terkirim dengan tarif all-in **Rp 2.540/kWh** yang sama. Harga per kWh-nya persis dan volumenya meleset sekitar 1,5 % (sesi di struk berhenti sedikit sebelum penuh). Hitungan refund juga cocok: beli Rp 152.448, terpakai Rp 145.694.
