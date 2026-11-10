# Nota ketepatan

[English](../accuracy.md) · [Bahasa Indonesia](../id/accuracy.md) · [简体中文](../zh-CN/accuracy.md) · **Bahasa Melayu**

## Perbandingan dengan resit

Analitik pengecasan ditentukur berdasarkan resit PLN Mobile sebenar milik pemilik:

| Sesi               | Dashboard             | Resit                 | Padanan |
| ------------------ | --------------------- | --------------------- | ------- |
| 58 → 100 %         | 28.9 kWh / Rp 73,491  | 28.94 kWh / Rp 73,521 | 99.9 %  |
| 35 → 80 %          | 29.1 kWh / Rp 73,981  | 29.23 kWh / Rp 74,273 | 99.6 %  |

Kecekapan cas DC dimodelkan bergantung pada SoC (mengecas hingga 100 % mengalami kehilangan lebih besar berbanding hingga 80 %), ditentukur daripada dua resit. Bateri boleh guna ≈ 58.9 kWh.

Perancang cas meramalkan jumlah yang sebenarnya anda bayar di meter, disemak terhadap resit SPKLU PLN Mobile sebenar:

| Perancang cas dalam aplikasi | Resit PLN Mobile sebenar |
| :---: | :---: |
| ![Anggaran perancang cas](../screenshots/accuracy-app.png) | ![Resit SPKLU PLN Mobile](../screenshots/accuracy-receipt.png) |

Aplikasi menganggarkan **58.2 kWh** untuk dibeli di meter pada **Rp 2,540/kWh**; resit menunjukkan **57.34 kWh** sebenarnya dihantar pada tarif semua-termasuk **Rp 2,540/kWh** yang sama. Harga per kWh adalah tepat dan isi padu tersasar sekitar 1.5 % (sesi pada resit berhenti sedikit sebelum penuh). Pengiraan bayaran balik juga sepadan: dibeli Rp 152,448, digunakan Rp 145,694.
