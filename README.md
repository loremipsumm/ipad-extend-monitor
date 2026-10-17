# iPad Extend Monitor

Jadikan iPad sebagai monitor kedua (Extend) atau cermin layar laptop (Duplicate) lewat WiFi:

- Video H.264 hardware (Intel QuickSync) di-decode hardware oleh Safari, dengan JPEG sebagai cadangan.
- Sentuhan, trackpad dan keyboard Magic Keyboard, serta Apple Pencil (tekanan + kemiringan) jadi input Windows asli.
- Audio laptop bisa ikut terdengar di iPad, dan clipboard tersinkron dua arah.
- Kamera iPad bisa dipakai sebagai webcam di Windows (Zoom, Meet, Teams).
- Monitor virtual terdeteksi otomatis dan ikut berputar ke portrait saat iPad diputar.

Semua berjalan native di Windows (bukan WSL).

## 1. Install

```bash
winget install --id=Python.Python.3.12 -e
```

```bash
winget install --id=VirtualDrivers.Virtual-Display-Driver -e
```

Lalu di folder ini (`E:\ext-mon`):

```bash
pip install -r requirements.txt
```

Di **Settings > System > Display**: pilih **Extend these displays**, set resolusi monitor virtual ke
resolusi iPad (iPad Air 11" = **2360 x 1640**) dan Scale 200%.

## 2. Jalankan

- **Paling mudah:** double-click `launcher.pyw`. Server jalan di background dengan ikon di system tray
  (pojok kanan bawah taskbar): klik untuk menyalin alamat iPad, buka log, restart, atau keluar.
  Klik kanan `launcher.pyw` → Send to → Desktop untuk membuat shortcut.
- **Atau di terminal:** `python server.py`

Server memilih monitor virtual secara otomatis, dan **me-restart dirinya sendiri** (±1–3 detik) setiap kali
susunan display berubah (Win+P, ganti resolusi, rotasi). iPad tersambung ulang otomatis.

## 3. Sekali saja: pasang sertifikat di iPad

Safari hanya mengaktifkan decoder video hardware di halaman HTTPS, jadi server membuat sertifikat lokal di
folder `certs\`:

1. Di Safari iPad, buka `http://IP-LAPTOP:5000/ca.crt` → **Allow**.
2. **Settings** → **Profile Downloaded** → **Install**.
3. **Settings > General > About > Certificate Trust Settings** → aktifkan **iPad Extend Monitor Local CA**.

Sertifikat ini hanya berlaku untuk IP jaringan lokal, jadi tidak bisa dipakai menyamar sebagai situs internet.
Jangan bagikan folder `certs\` (berisi kunci privat).

## 4. Pakai di iPad

Buka `https://IP-LAPTOP:5001/` di Safari (pegang iPad landscape). Untuk full-screen: Share → **Add to Home Screen**.
Layar iPad tidak akan mati sendiri selama halaman ini terbuka.

**Menu ☰ (kanan bawah):** ganti mode Extend/Duplicate, keyboard layar, audio laptop, kirim clipboard iPad ke
Windows, dan statistik (fps, bitrate, waktu decode, ping).

| Jari di layar | Hasil di Windows |
|---|---|
| Tap | Klik kiri (tap dua kali cepat = double-click) |
| Tap lalu geser | Drag |
| Tahan ±0,5 detik | Klik kanan |
| Dua jari geser | Scroll (arah natural seperti di iPad) |
| Pinch | Zoom (Ctrl+scroll) |
| Tap dua jari | Klik kanan |

**Trackpad Magic Keyboard:** gerak = kursor, klik = klik kiri (tahan untuk drag), klik dua jari = klik kanan,
geser dua jari = scroll.

**Keyboard Magic Keyboard:** langsung mengetik di Windows. Cmd = Ctrl (Cmd+C/V/Z/S/A), Option = Alt,
**Option + Z = buka Start**. Pintasan sistem iPadOS (Cmd+Tab, Cmd+Space, Globe) tetap diambil iPad. Buka
halaman dengan `?debug` untuk melihat tombol apa saja yang diterima Safari.

**Apple Pencil:** jadi pen Windows dengan tekanan dan kemiringan, untuk Krita, Photoshop, OneNote, dll.

**Clipboard:** teks yang disalin di Windows masuk ke clipboard iPad pada ketukan berikutnya. Dari iPad ke
Windows: menu ☰ → *Kirim clipboard iPad → Windows*, lalu Cmd+V.

**Audio:** menu ☰ → *Audio laptop*. Suara laptop diputar di iPad (±0,1 detik terlambat dari gambar).

**Rotasi:** di mode Extend, memutar iPad ke portrait ikut memutar monitor virtual (server restart sebentar).

**Kamera iPad sebagai webcam Windows:** butuh **OBS Studio** (`winget install --id=OBSProject.OBSStudio -e`;
OBS tidak perlu dibuka). Menu ☰ → *Kamera iPad → webcam Windows* (ketuk berulang: kamera depan → belakang
→ mati), izinkan akses kamera di Safari, lalu pilih **OBS Virtual Camera** di Zoom/Meet/Teams. Hanya
video (mikrofon tetap dari laptop), dan kamera hanya aktif selama halaman ini terbuka di layar iPad.

## config.json

| Kunci | Arti |
|---|---|
| `extend_display` | `null` = deteksi otomatis. Isi nama display (mis. `"\\\\.\\DISPLAY16"`, lihat `/monitors`) untuk memaksa |
| `auto_rotate` | `true` = monitor virtual ikut orientasi iPad |
| `port` / `https_port` | Port JPEG (HTTP) dan H.264 (HTTPS) |
| `h264_quality` | 18–28; kecil = lebih tajam tapi lebih berat |
| `jpeg_quality` | Kualitas mode JPEG cadangan |

## Catatan

- Delay paling besar biasanya dari WiFi. Pakai jaringan **5 GHz** (bukan 2,4 GHz) untuk laptop dan iPad,
  atau Mobile Hotspot Windows. Ping di menu Statistik idealnya di bawah 10 ms.
- Kursor digambar langsung di iPad, jadi selalu bergerak seketika mengikuti jari/trackpad; isi layar
  (window yang di-drag, scroll) tetap mengikuti kecepatan jaringan.

- Tanpa autentikasi: siapa pun di WiFi yang sama bisa mengendalikan laptop lewat server ini. Jangan pakai di
  WiFi publik/kantor yang tidak dipercaya.
- Mode Duplicate diatur aplikasi ini, bukan lewat Win+P. Kalau Windows diset ke Duplicate, hanya mode
  Duplicate yang tersedia.
- Log server ada di `server.log` kalau dijalankan lewat `launcher.pyw`.
