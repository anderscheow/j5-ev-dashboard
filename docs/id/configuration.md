# Konfigurasi dan mobil yang dikenal

[English](../configuration.md) · **Bahasa Indonesia** · [简体中文](../zh-CN/configuration.md) · [Bahasa Melayu](../ms/configuration.md)

Halaman login dashboard dan `tools/setup.py` mengisi sebagian besar pengaturan secara otomatis. Referensi ini mencakup field opsional dan default untuk mobil yang sudah dikenal.

## Referensi `creds.json`

| key | wajib | keterangan |
| --- | --- | --- |
| `email`, `password` | ✅ | login CarLinko-mu (plaintext lewat TLS; hanya disimpan lokal) |
| `region` | | region API, default `sea` (Asia Tenggara). **Login gagal kalau region salah** — akun yang dibuat di Eropa/Timur Tengah/Afrika butuh `emea` (dilaporkan pada J5 Noble di Israel, [#6](https://github.com/GodrezJr2/j5-ev-dashboard/issues/6)). Kalau password benar tapi ditolak, coba region lain dulu sebelum yang lain |
| `vehicle_id`, `device_sn` | otomatis | id kendaraan + serial perangkat — **`setup.py` mengisinya untukmu** |
| `vehicle` | otomatis | `{plate, model, vin}` — terdeteksi otomatis; UI menyembunyikan plat dan VIN secara default |
| `battery_kwh`, `wltp_kwh_100` | | kapasitas pack yang terpakai + acuan WLTP. **Mobil tidak pernah melaporkan ukuran pack**, jadi nilainya dari kamu atau dari [tabel mobil yang dikenal](#mobil-yang-dikenal); model yang belum dikenal ditanyakan saat setup dan diberi label *assumed* di UI sampai kamu mengaturnya. Nilai ini menskalakan semua angka kWh, biaya, dan efisiensi |
| `currency` | | `{symbol, locale, code}` untuk negara non-IDR, mis. `{"symbol":"R","locale":"en-ZA","code":"ZAR"}`. Default `Rp` / `id-ID` |
| `tariff`, `petrol_price`, `petrol_kml` | | tarif pengisian per kWh + harga bensin per liter + konsumsi, dalam mata uangmu |
| `tyre_unit` | | `psi` (default), `bar`, atau `kpa` untuk tampilan ban |
| `tpms_scale` | | skala byte ban mentah → kPa. J5 memakai sistem tidak langsung (tanpa PSI asli); mobil yang mengirim tekanan asli perlu dikalibrasi ulang (lihat di bawah) |
| `car_image` | otomatis | override gambar hero dashboard. Biasanya tidak perlu — `setup.py` menyimpan render yang di-host CarLinko untuk mobil *kamu* (model dan warna yang benar) dan server men-cache-nya di `/car-photo`. Isi dengan nama file di `web/` atau URL lengkap hanya kalau mau memakai fotomu sendiri |
| `specs` | | angka brosur untuk kartu **Specifications** — `{label, source, performance:[[name,value,unit]], dimensions:[…]}`. Hanya milik J5 yang disertakan; model lain menyembunyikan kartu ini daripada menampilkan angka mobil lain, jadi isi kalau kamu mau menampilkannya |
| `powertrain` | | `auto` (default), `bev`, atau `phev`. Pada `auto`, mobil dianggap PHEV begitu melaporkan tangki bahan bakar — BEV tidak pernah melaporkannya, jadi ini tetap mati kecuali memang seharusnya aktif |
| `chemistry` | | `lfp` (default) atau `nmc` — menentukan saran perawatan baterai |
| `full_charge_days` | | seberapa sering disarankan mengisi sampai 100 %. Default `7` untuk LFP, `90` untuk NMC |
| `gmaps_key` | | key Google Maps — mengaktifkan perencana perjalanan + peta SPKLU (kalau tidak, fallback ke OSM) |
| `dashboard_password` | | set (halaman login → Advanced) untuk mengunci dashboard dengan password — **lakukan ini kalau URL bisa dijangkau dari internet** |

> **Tidak di Indonesia? `setup.py` sekarang bertanya.** Ada preset untuk Indonesia, Afrika Selatan, Inggris, dan Eurozone (dan kamu bisa mengetik mata uang lain), lalu menanyakan tarif pengisian, harga bensin, dan satuan ban — jadi semua biaya di dashboard sudah dalam mata uangmu tanpa edit `creds.json` manual. Jalankan ulang `python setup.py` kapan saja untuk mengubahnya; nilai yang sudah ada dipertahankan. Perencana perjalanan + peta charger masih khusus Indonesia (memakai data SPKLU/PLN).
>
> Harga bawaan hanyalah *titik awal* dan bisa kedaluwarsa — default Indonesia adalah Pertamax Rp16.250/L dan SPKLU PLN Rp2.540/kWh (Jul 2026). Set `petrol_price` / `tariff` sesuai harga di tempatmu.

> **`tariff_idr` adalah nama lama `tariff`** — masih dibaca, jadi konfigurasi lama tetap jalan, tapi sebaiknya pakai `tariff` karena biaya sekarang tidak khusus Indonesia.
>
> **Tekanan ban sekarang dikalibrasi otomatis.** CarLinko menerbitkan rumus ban per model (`appKpaFormula`, mis. `data * 1.373`), jadi `setup.py` membaca skala mobilmu langsung dari API — tanpa kalibrasi manual. Kalau kamu sudah mengatur `tpms_scale` sendiri, setup mempertahankan nilaimu dan hanya menyebut apa kata CarLinko; hapus key-nya untuk memakai milik CarLinko. Pilih satuan tampilan dengan `tyre_unit` (`psi`/`bar`/`kpa`).
>
> Kalau angkanya masih terlihat salah, kartu ban menampilkan **byte mentah** — baca satu roda dari layar mobilmu sendiri dan `tpms_scale = kPa_kamu / byte_mentah`. Perlu diingat sebagian mobil (termasuk J5) memakai TPMS *tidak langsung* dan hanya melaporkan **status** ban, tidak pernah tekanan asli.

## Mobil yang dikenal

Pengaturan yang sudah dikonfirmasi pemilik lain. Tambahkan mobilmu lewat [laporan kompatibilitas](https://github.com/GodrezJr2/j5-ev-dashboard/issues/new?template=compatibility.md).

Pengaturan ini **diterapkan otomatis** dari model yang terdeteksi — tabelnya ada di [`tools/known_cars.py`](../../tools/known_cars.py), dan apa pun yang kamu set di `creds.json` tetap menang.

| Mobil | `battery_kwh` | `tpms_scale` | `tyre_unit` | Catatan |
| --- | --- | --- | --- | --- |
| Jaecoo J5 EV (ID) | `58.9` | n/a | `psi` | Mobil acuan. TPMS *tidak langsung* — hanya melaporkan **status** ban, tidak pernah tekanan |
| Chery Tiggo 8 PHEV 2025 (ZA) | `18.3` | `1.779` | `bar` | TPMS langsung (mentah `136` = 2,42 bar). PHEV — level bahan bakar, konsumsi bahan bakar, dan jarak tempuh bahan bakar semuanya sudah didecode ([#2](https://github.com/GodrezJr2/j5-ev-dashboard/issues/2)) |
| Chery Tiggo 7 PHEV (MY) | `18.3` | `1.779` | `psi` | Pack dan skala ban sama dengan Tiggo 8 PHEV, dikonfirmasi secara independen ([#3](https://github.com/GodrezJr2/j5-ev-dashboard/issues/3)). Malaysia menamainya **`TIGGO 7 CSH`** — cocok otomatis |
| Omoda E5 2025 (UY) | `61` | — | — | LFP, WLTP 15,5 kWh/100 km — dikonfirmasi pemilik ([#5](https://github.com/GodrezJr2/j5-ev-dashboard/issues/5)). Semua berfungsi termasuk login, deteksi otomatis, dan pengisian |

**Kalau mobilmu tidak terdaftar**, tidak ada yang dikarang atas namamu: `setup.py` menanyakan ukuran pack, kartu baterai memberi label *assumed* sampai kamu mengaturnya, dan kartu spesifikasi tetap tersembunyi daripada menampilkan angka brosur mobil lain.

**PHEV.** % tangki bahan bakar (byte 21), konsumsi bahan bakar L/100 km (byte 53), dan **jarak tempuh bahan bakar** (byte 70–71) sudah didecode dan ditampilkan di samping baterai. Byte itu bernilai `0` di setiap frame BEV, jadi pemilik BEV tidak melihat UI bahan bakar. `range_km` tetap **khusus EV** dan diberi label *EV range* di PHEV; angka gabungan yang ditampilkan dasbor mobil **tidak dikirim** — nilainya persis EV + bahan bakar (diverifikasi pada tiga capture), jadi app menghitungnya dan menyebutkannya, bukan mengklaimnya sebagai angka dari mobil. Karena PHEV juga menempuh jarak dengan bensin, kWh/100 km dan penghematan vs bensin hanya perkiraan.
