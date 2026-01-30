# CCTV Playback Downloader

Script Python untuk mengunduh rekaman playback dari DVR/NVR secara otomatis menggunakan Playwright.

## Fitur

- Login otomatis ke sistem DVR/NVR
- Download rekaman dari semua channel (1-21)
- **Organisasi file REAL-TIME** - file langsung dipindahkan saat selesai download
- **Auto-retry** - jika ada file gagal, otomatis retry sampai 3x
- **Skip file yang sudah ada** - tidak download ulang file yang sudah tersimpan
- Logging lengkap ke file
- Support pagination untuk banyak file
- Graceful shutdown dengan CTRL+C

## Instalasi

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright Browser

Setelah install playwright, Anda perlu install browser:

```bash
playwright install chromium
```

Atau untuk install semua browser:

```bash
playwright install
```

## Konfigurasi

Edit file `playback_downloader.py` untuk mengubah:

- **Host IP**: Ubah pada baris `DeviceScraper("192.168.88.19")`
- **Username/Password**: Ubah pada parameter `login("scrapper", "sc@10001")`
- **Range Channel**: Ubah filter channel pada fungsi `download_playback()`

## Penggunaan

Jalankan script:

```bash
python playback_downloader.py
```

Script akan:
1. Login ke sistem DVR/NVR
2. Masuk ke menu playback download
3. Loop untuk setiap channel aktif (1-21)
4. Download rekaman kemarin (24 jam terakhir)
5. Organisasi file ke folder `downloads/cctv/channelX/`

## Struktur Folder

```
.
├── playback_downloader.py    # Script utama
├── requirements.txt           # Dependencies
├── cookies.json              # Session cookies (auto-generated)
├── storage.json              # LocalStorage (auto-generated)
└── downloads/
    ├── log.txt               # Log file
    ├── 192.168.88.19_1_20250129143000_20250129144500.mp4  # File mentah (sementara)
    └── cctv/                 # File terorganisir
        ├── channel1/         # File dari channel 1
        │   └── 2025-01-29.14-30-00_14-45-00.mp4
        ├── channel2/         # File dari channel 2
        └── ...
```

**PENTING - Lokasi File Download:**
1. File langsung diorganisir **REAL-TIME** saat selesai download
2. File **LANGSUNG MASUK** ke folder `downloads/cctv/channelX/` 
3. **TIDAK PERLU NUNGGU PROGRAM SELESAI** - cek folder channel kapan saja!
4. Log di `downloads/log.txt` akan show:
   - `[*] Downloading: filename.mp4` - file sedang didownload
   - `[+] File saved: filename.mp4` - file tersimpan
   - `[✓] Organized: ... → channelX/...` - file sudah dipindah & rename

## Format Nama File

File yang diunduh akan diorganisir dengan format:

```
YYYY-MM-DD.HH-MM-SS_HH-MM-SS.mp4
└─Tanggal  └─Waktu    └─Waktu
           Mulai      Selesai
```

**Contoh:** `2026-01-29.00-42-31_00-45-52.mp4`
- **Tanggal:** 2026-01-29 (29 Januari 2026)
- **Waktu mulai:** 00:42:31 (Jam 00:42:31 atau 12:42:31 AM)
- **Waktu selesai:** 00:45:52 (Jam 00:45:52 atau 12:45:52 AM)
- **Durasi:** ~3 menit 21 detik

**Cara Baca File Anda:**
```bash
2026-01-29.00-42-31_00-45-52.mp4
```
- Rekaman tanggal **29 Januari 2026**
- Dimulai jam **00:42:31** (tengah malam lewat 42 menit)
- Selesai jam **00:45:52** (tengah malam lewat 45 menit)
- Durasi rekaman sekitar **3 menit**

## Troubleshooting

### File tidak terdownload
**Cek hal berikut:**
1. Lihat di folder `downloads/cctv/channelX/` - bukan di `downloads/` langsung
2. Periksa log di `downloads/log.txt` untuk melihat:
   - Apakah ada pesan `[+] File downloaded: ...`
   - Apakah ada pesan `[+] Organized: ...`
3. Pastikan ada rekaman di tanggal yang dicari
4. Coba jalankan dengan headless=False untuk melihat browser:
   ```python
   # Di file playback_downloader.py, line ~90
   headless=False  # Ganti dari True ke False
   ```

### Browser tidak terbuka
Pastikan Playwright browser sudah terinstall:
```bash
playwright install chromium
```

### Download tidak jalan
- Cek koneksi ke IP DVR/NVR
- Pastikan username/password benar
- Periksa log di `downloads/log.txt`

### Permission Error
Pastikan folder `downloads/` memiliki permission write

## Catatan

- Script menggunakan headless browser (tidak tampil UI)
- Timeout default download: 10 menit per halaman
- **Auto-retry:** Jika ada file gagal, script otomatis retry page tersebut (max 3x)
- **Skip duplicates:** File yang sudah ada tidak akan didownload ulang
- Log lengkap tersimpan di `downloads/log.txt`
- Log akan menunjukkan:
  - `[SKIP] File already exists: ...` - file sudah ada, dilewati
  - `[RETRY] Attempt 2/3 for page X` - sedang retry
  - `[✓] Organized: ... → channelX/...` - file berhasil diorganisir

## Stop Script

Tekan `CTRL+C` untuk menghentikan script dengan aman. Browser akan ditutup otomatis.
