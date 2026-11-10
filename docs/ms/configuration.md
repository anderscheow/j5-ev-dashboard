# Konfigurasi dan kereta yang dikenali

[English](../configuration.md) · [Bahasa Indonesia](../id/configuration.md) · [简体中文](../zh-CN/configuration.md) · **Bahasa Melayu**

Halaman log masuk dashboard dan `tools/setup.py` mengisi kebanyakan tetapan secara automatik. Rujukan ini meliputi medan pilihan dan nilai lalai untuk kereta yang dikenali.

## Rujukan `creds.json`

| kunci | wajib | keterangan |
| --- | --- | --- |
| `email`, `password` | ✅ | log masuk CarLinko anda (teks biasa melalui TLS; disimpan secara setempat sahaja) |
| `region` | | rantau API, lalai `sea` (Asia Tenggara). **Log masuk gagal jika rantau salah** — akaun yang dibuat di Eropah/Timur Tengah/Afrika memerlukan `emea` (dilaporkan pada J5 Noble di Israel, [#6](https://github.com/GodrezJr2/j5-ev-dashboard/issues/6)). Jika kata laluan betul tetapi ditolak, cuba rantau lain dahulu sebelum perkara lain |
| `vehicle_id`, `device_sn` | auto | ID kenderaan + nombor siri peranti — **`setup.py` mengisinya untuk anda** |
| `vehicle` | auto | `{plate, model, vin}` — dikesan secara automatik; UI menyembunyikan plat dan VIN secara lalai |
| `battery_kwh`, `wltp_kwh_100` | | saiz bateri boleh guna + rujukan WLTP. **Kereta tidak pernah melaporkan saiz bateri**, jadi nilainya datang daripada anda atau [jadual kereta yang dikenali](#kereta-yang-dikenali); model yang tidak dikenali ditanya semasa persediaan dan dilabel *assumed* dalam UI sehingga anda menetapkannya. Ia menskalakan setiap angka kWh, kos dan kecekapan |
| `currency` | | `{symbol, locale, code}` untuk negara bukan IDR, cth. `{"symbol":"R","locale":"en-ZA","code":"ZAR"}`. Lalai `Rp` / `id-ID` |
| `tariff`, `petrol_price`, `petrol_kml` | | tarif pengecasan setempat per kWh + harga petrol per liter + penggunaan bahan api, dalam mata wang anda |
| `tyre_unit` | | `psi` (lalai), `bar`, atau `kpa` untuk paparan tayar |
| `tpms_scale` | | skala bait tayar mentah → kPa. J5 ialah tidak langsung (tiada PSI sebenar); kereta yang menghantar tekanan sebenar perlu ditentukur semula (lihat di bawah) |
| `car_image` | auto | gantian untuk gambar hero dashboard. Biasanya tidak perlu — `setup.py` menyimpan imej yang dihoskan CarLinko untuk kereta *anda* (model dan warna yang betul) dan pelayan menyimpannya dalam cache di `/car-photo`. Tetapkan kepada nama fail dalam `web/` atau URL penuh hanya jika anda lebih suka menggunakan foto sendiri |
| `specs` | | angka brosur untuk kad **Specifications** — `{label, source, performance:[[name,value,unit]], dimensions:[…]}`. Hanya milik J5 disertakan; model lain menyembunyikan kad ini dan bukannya memaparkan angka kereta lain, jadi isikannya jika anda mahu |
| `powertrain` | | `auto` (lalai), `bev` atau `phev`. Pada `auto`, kereta dianggap PHEV sebaik sahaja ia melaporkan tangki bahan api — BEV tidak pernah melaporkannya, jadi ini kekal mati melainkan memang perlu dihidupkan |
| `chemistry` | | `lfp` (lalai) atau `nmc` — menentukan nasihat penjagaan bateri |
| `full_charge_days` | | kekerapan mengesyorkan cas 100 %. Lalai `7` untuk LFP, `90` untuk NMC |
| `gmaps_key` | | kunci Google Maps — mengaktifkan perancang perjalanan + peta SPKLU (jika tidak, kembali kepada OSM) |
| `dashboard_password` | | tetapkan (halaman log masuk → Advanced) untuk mengunci dashboard dengan kata laluan — **lakukan ini jika URL boleh dicapai dari internet** |

> **Bukan di Indonesia? `setup.py` kini bertanya.** Ia menawarkan pratetap untuk Indonesia, Afrika Selatan, UK dan Zon Euro (dan membenarkan anda menaip mata wang lain), kemudian bertanya tarif pengecasan, harga petrol dan unit tayar — supaya setiap kos pada dashboard dalam mata wang anda tanpa menyunting `creds.json` secara manual. Jalankan semula `python setup.py` pada bila-bila masa untuk mengubahnya; nilai sedia ada dikekalkan. Perancang perjalanan + peta pengecas masih khusus Indonesia (menggunakan data SPKLU/PLN).
>
> Harga terbina dalam hanyalah *titik permulaan* dan akan lapuk — nilai lalai Indonesia ialah Pertamax Rp16,250/L dan SPKLU PLN Rp2,540/kWh (Jul 2026). Tetapkan `petrol_price` / `tariff` kepada harga di tempat anda.

> **`tariff_idr` ialah nama lama `tariff`** — masih dibaca, jadi konfigurasi sedia ada terus berfungsi, tetapi lebih baik guna `tariff` kerana kos kini tidak khusus Indonesia.
>
> **Tekanan tayar kini ditentukur untuk anda.** CarLinko menerbitkan formula tayar bagi setiap model (`appKpaFormula`, cth. `data * 1.373`), jadi `setup.py` membaca skala kereta anda terus daripada API — tanpa penentukuran manual. Jika anda sudah menetapkan `tpms_scale` sendiri, persediaan mengekalkan nilai anda dan hanya menyebut apa yang CarLinko katakan; padam kunci itu untuk menggunakan nilai mereka. Pilih unit paparan dengan `tyre_unit` (`psi`/`bar`/`kpa`).
>
> Jika angka masih nampak salah, kad tayar memaparkan **bait mentah** — baca satu tayar daripada skrin kereta anda sendiri dan `tpms_scale = kPa_anda / bait_mentah`. Perhatikan bahawa sesetengah kereta (termasuk J5) mempunyai TPMS *tidak langsung* dan hanya melaporkan **status** tayar, tidak pernah tekanan sebenar.

## Kereta yang dikenali

Tetapan yang telah disahkan oleh pemilik lain. Sila tambah kereta anda melalui [laporan keserasian](https://github.com/GodrezJr2/j5-ev-dashboard/issues/new?template=compatibility.md).

Tetapan ini **digunakan secara automatik** daripada model yang dikesan — jadualnya terdapat dalam [`tools/known_cars.py`](../../tools/known_cars.py), dan apa-apa yang anda tetapkan dalam `creds.json` tetap diutamakan.

| Kereta | `battery_kwh` | `tpms_scale` | `tyre_unit` | Nota |
| --- | --- | --- | --- | --- |
| Jaecoo J5 EV (ID) | `58.9` | n/a | `psi` | Kereta rujukan. TPMS *tidak langsung* — hanya melaporkan **status** tayar, tidak pernah tekanan |
| Chery Tiggo 8 PHEV 2025 (ZA) | `18.3` | `1.779` | `bar` | TPMS langsung (mentah `136` = 2.42 bar). PHEV — paras bahan api, penggunaan bahan api dan jarak bahan api semuanya telah dinyahkod ([#2](https://github.com/GodrezJr2/j5-ev-dashboard/issues/2)) |
| Chery Tiggo 7 PHEV (MY) | `18.3` | `1.779` | `psi` | Pek bateri dan skala tayar sama dengan Tiggo 8 PHEV, disahkan secara bebas ([#3](https://github.com/GodrezJr2/j5-ev-dashboard/issues/3)). Malaysia melabelnya **`TIGGO 7 CSH`** — dipadankan secara automatik |
| Omoda E5 2025 (UY) | `61` | — | — | LFP, WLTP 15.5 kWh/100 km — disahkan pemilik ([#5](https://github.com/GodrezJr2/j5-ev-dashboard/issues/5)). Semuanya berfungsi termasuk log masuk, pengesanan automatik dan pengecasan |

**Jika kereta anda tiada dalam senarai**, tiada apa yang direka bagi pihak anda: `setup.py` bertanya saiz bateri, kad bateri melabelnya *assumed* sehingga anda menetapkannya, dan kad spesifikasi kekal tersembunyi dan bukannya memaparkan angka brosur kereta lain.

**PHEV.** % tangki bahan api (bait 21), penggunaan bahan api L/100 km (bait 53) dan **jarak bahan api** (bait 70–71) telah dinyahkod dan dipaparkan di sebelah bateri. Bait itu bernilai `0` pada setiap frame BEV, jadi pemilik BEV tidak melihat UI bahan api. `range_km` kekal **EV sahaja** dan dilabel *EV range* pada PHEV; angka gabungan yang dipaparkan papan pemuka kereta **tidak dihantar** — ia tepat EV + bahan api (disahkan pada tiga tangkapan), jadi aplikasi mengiranya dan menyatakannya dan bukannya menganggapnya sebagai angka daripada kereta. Memandangkan PHEV juga menempuh jarak dengan petrol, kWh/100 km dan penjimatan berbanding petrol hanyalah anggaran.
