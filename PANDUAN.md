# Aplikasi Warkah — Kantor Pertanahan Kabupaten Bone Bolango

Katalog arsip warkah berbentuk perpustakaan: **cari** bidangnya, **pinjam/kembalikan**
warkahnya, dan **pantau** progres inventarisasi per desa, kecamatan, dan petugas.

## Menjalankan

Klik dua kali **`jalankan.bat`**, biarkan jendelanya terbuka selama aplikasi dipakai.

- Di komputer server: <http://localhost:8000>
- Dari komputer lain di jaringan kantor: `http://<alamat-ip-server>:8000`
  (alamat IP-nya ditampilkan otomatis oleh `jalankan.bat`)

Menghentikan: tekan `Ctrl+C` di jendela tersebut, atau tutup jendelanya.

Tidak perlu memasang apa pun — hanya Python yang sudah ada di komputer ini.

### Supaya bisa diakses dari komputer lain

Sekali saja, jalankan di **Command Prompt sebagai Administrator**:

```
netsh advfirewall firewall add rule name="Aplikasi Warkah" dir=in action=allow protocol=TCP localport=8000
```

## Masuk aplikasi

Setiap orang punya akun sendiri. Sesi berlaku 12 jam, lalu diminta masuk lagi.

Ada dua peran:

| Peran | Bisa apa |
|---|---|
| **Admin** | Semua: mengisi lokasi simpan & pemeriksaan **atas nama petugas mana pun**, mengatur penugasan desa, dan mengelola akun pengguna |
| **Petugas** | Mencari di katalog, mengisi lokasi simpan & pemeriksaan, mencatat pinjam/kembali, melihat monitoring. **Kolom "Petugas pemeriksa" terisi otomatis dengan namanya sendiri** dan tidak bisa diubah — tidak perlu memilih petugas lagi setiap input |

Petugas bisa melihat halaman penugasan desa, tetapi hanya Admin yang bisa mengubahnya.

### Mengelola akun

Admin membuka menu **Pengguna**: menambah akun, mengatur ulang sandi, mengubah peran,
serta menonaktifkan/mengaktifkan akun. Sandi awal dibuat acak dan **hanya ditampilkan
satu kali** setelah akun dibuat — catat lalu sampaikan ke orangnya.

Setiap pengguna mengganti sandinya sendiri lewat menu **Ganti sandi** (minimal 8 karakter).

Bisa juga dari terminal:

```
python kelola_pengguna.py daftar                     lihat semua akun
python kelola_pengguna.py tambah "Nama" username Petugas
python kelola_pengguna.py sandi username             atur ulang sandi
python kelola_pengguna.py peran username Admin
python kelola_pengguna.py nonaktif username
```

Admin aktif terakhir tidak bisa dinonaktifkan atau diturunkan perannya, supaya aplikasi
tidak pernah kehilangan pengelola.

Akun yang dinonaktifkan tidak bisa masuk, tetapi catatan pemeriksaan dan penugasan atas
namanya tetap tersimpan.

## Isi aplikasi

| Menu | Kegunaan |
|---|---|
| **Beranda** | Ringkasan koleksi, kualitas data (KW), peminjaman lewat jatuh tempo, aktivitas terakhir |
| **Katalog** | Cari bidang dan lihat lokasi fisik warkahnya. Saring per kecamatan, desa, jenis hak, KW, ketersediaan, status pemeriksaan, dan petugas pemeriksa |
| **Sirkulasi** | Daftar warkah yang sedang keluar, yang lewat jatuh tempo, dan riwayat pengembalian |
| **Monitoring** | Rekap capaian per kecamatan dan per petugas; penugasan desa ke petugas |
| **Rekap** | Jumlah bidang terinput per desa per kecamatan menurut kode, lengkap dengan pecahan KW, untuk dicocokkan dengan tarikan KKP |
| **Impor** *(Admin)* | Unggah berkas Excel/CSV hasil tarikan KKP untuk menambah atau memperbarui data bidang |
| **Pengguna** *(Admin)* | Membuat akun, mengatur ulang sandi, dan mengubah peran |

**Rekap per petugas** dihitung dari **siapa yang mengisi kolom Petugas pada pemeriksaan**,
bukan dari penugasan desa. Untuk tiap petugas ditampilkan jumlah bidang yang diperiksa,
jumlah desa & kecamatan, **rentang tanggal periksa** (tanggal pertama s/d terakhir),
jumlah hari kerja, serta **daftar desa yang diperiksa** beserta jumlah bidang per desa.
Klik angka atau nama desa untuk membuka daftar bidangnya di Katalog. Kolom penugasan
(desa ditugaskan / selesai / progres) tetap ada sebagai pembanding target. Bidang yang
sudah diperiksa tetapi kolom petugasnya kosong dilaporkan di catatan bawah tabel.

**Saringan tanggal.** Di atas tabel ada isian **Tanggal periksa dari** dan **Sampai**.
Isi salah satu atau keduanya lalu klik **Terapkan**: kolom *Diperiksa* pada rekap
kecamatan dan seluruh rekap petugas hanya menghitung pemeriksaan yang tanggalnya masuk
periode itu. Pemeriksaan yang kolom tanggalnya kosong tidak ikut selama periode dipakai.
Klik **Semua periode** untuk kembali ke seluruh data. Kolom penugasan desa sengaja tidak
ikut disaring karena sifatnya target, bukan hasil harian.

**Laporan PDF.** Tombol **PDF** di setiap baris petugas mencetak laporan petugas itu
sesuai periode yang sedang dipilih; tombol **PDF semua petugas** mencetak semuanya
sekaligus, satu petugas satu halaman. Berkasnya terbuka di tab baru dan bisa langsung
disimpan atau dicetak dari peramban. Isinya:

| Bagian | Isi |
|---|---|
| Kepala | Nama petugas, periode laporan, rentang tanggal periksa, hari kerja, jumlah bidang/desa/kecamatan, rata-rata bidang per hari |
| **A. Kelengkapan berkas** | Matriks buku tanah / surat ukur / warkah lawan kondisinya (Ada, Tidak Ada, Belum dicek, dan nilai lain bila terpakai) beserta persen "Ada", ditutup kalimat berapa bidang yang buku tanah **dan** surat ukurnya lengkap serta berapa yang masih perlu ditelusuri |
| **B. Desa yang diperiksa** | Per desa: kecamatan, kode desa, jumlah bidang, jumlah BT ada, SU ada, warkah ada, tanggal periksa pertama dan terakhir, plus baris JUMLAH |
| **C. Rincian status identifikasi** | Jumlah dan porsi tiap status (Lengkap, Tidak Lengkap, dan seterusnya) |
| Penutup | Ruang tanda tangan petugas; kaki halaman berisi waktu cetak, nama pencetak, dan nomor halaman |

Kolom **BT ada / SU ada / Warkah ada** menghitung bidang yang berkas fisiknya ditemukan
ada. Angka warkah akan tetap nol selama kolom *Warkah* belum diisi — bilah centang cepat
di Katalog hanya mengisi buku tanah dan surat ukur, warkah baru bisa dicatat lewat
halaman detail bidang.

### Rekap bidang & pencocokan dengan KKP

Menu **Rekap** menjawab satu pertanyaan: *berapa bidang yang sudah masuk aplikasi, dan
apakah jumlahnya sama dengan yang ada di KKP?* Tabelnya disusun per kecamatan menurut
kode, lalu per desa menurut kode desa:

| Kolom | Isi |
|---|---|
| Kode desa | Kode 8 digit dari KKP, dipakai sebagai patokan pencocokan |
| Desa | Nama desa; klik untuk membuka daftar bidangnya di Katalog |
| Bidang | Jumlah bidang yang **sudah terinput** di aplikasi |
| KW1 – KW6 | Pecahan bidang menurut kualitas datanya, mengikuti nilai KW dari KKP |
| Tanpa KW | Bidang yang kolom KW-nya kosong |
| Jumlah KKP | Angka pembanding yang **diketik manual** dari aplikasi KKP (Admin) |
| Selisih | Bidang − Jumlah KKP; nol berarti sudah cocok |
| Catatan | Keterangan bebas, mis. tanggal tarikan KKP yang dipakai |

Setiap kecamatan ditutup baris **JUMLAH**, dan seluruh tabel ditutup baris **JUMLAH
SELURUHNYA** — itulah angka total bidang terinput yang dipakai untuk pencocokan besar.

**Cara mencocokkan.** Tarik rekap per desa dari KKP, lalu isi kolom *Jumlah KKP* untuk
tiap desa dan tekan **Simpan angka KKP**. Desa yang selisihnya nol ditandai hijau
*cocok*, yang belum ditandai merah dengan angka selisihnya. Setelah itu pakai saringan
**Kecocokan → Selisihnya belum nol** untuk melihat desa mana saja yang masih perlu
ditindaklanjuti; selisih negatif berarti masih ada data KKP yang belum masuk, dan
berkas desanya bisa langsung diunggah lewat menu **Impor**.

Saringan lain yang tersedia: kelompok kode (18XX / 30XX), kecamatan menurut kodenya,
serta *Sudah cocok*, *Angka KKP belum diisi*, dan *Belum ada bidang sama sekali*.

**Unduh CSV** mengambil tabel yang sedang tampil — ikut saringan yang sedang dipakai —
berikut subtotal kecamatan dan jumlah seluruhnya. Berkasnya bertitik koma dan ber-BOM
UTF-8 sehingga langsung rapi saat dibuka di Excel, dan bisa disandingkan kolom demi
kolom dengan tarikan KKP.

Semua petugas boleh membuka halaman ini; hanya Admin yang bisa mengisi atau mengubah
angka pembanding KKP. Mengosongkan isian lalu menyimpan akan menghapus angka
pembanding desa itu.

### Impor data lewat halaman web

Menu **Impor** (hanya Admin) dipakai bila ada data yang belum masuk aplikasi, misalnya
desa baru, hak baru, atau tarikan KKP yang diperbarui. Tidak perlu masuk ke server dan
menjalankan `import_data.py`.

**Berkas yang bisa diunggah** — sama persis dengan isi folder `per_desa`:

- satu berkas per desa, bernama `HM_<kodedesa>_<NAMA_DESA>.xlsx`
  (contoh `HM_18040101_HUNTU.xlsx`) — kode dan nama desa dibaca dari nama berkasnya;
- atau berkas gabungan yang sudah punya kolom `Kode_Desa` dan `Nama_Desa`
  (contoh `Gabungan_Hak_Milik_18XX.xlsx`) — isinya dipecah per desa otomatis.

Format `.xlsx`, `.xlsm`, `.xls`, dan `.csv` diterima; boleh memilih banyak berkas
sekaligus (sampai 400). Kolom yang dibaca: `Nomor_Hak` (wajib), `Tipe_Hak`,
`Surat_Ukur`, `NIB`, `Luas`, `Produk`, `Luas_Peta`, `Validator_Tekstual`,
`Validator_Peta`, `Blokir_Internal`, `KW`, `Pemilik_Pertama`, `Pemilik_Akhir`.
Besar-kecil huruf, spasi, dan titik pada nama kolom diabaikan; kolom yang tidak ada
diisi kosong; `Tipe_Hak` kosong dianggap `BT1` (Hak Milik).

**Tiga cara menulis data:**

| Mode | Yang terjadi |
|---|---|
| Tambah bidang baru dan perbarui yang sudah ada *(bawaan)* | Nomor hak baru ditambahkan, nomor hak lama disegarkan isinya |
| Hanya tambah bidang baru | Data lama sama sekali tidak diubah |
| Ganti isi desa | Seperti di atas, ditambah membuang bidang desa itu yang sudah tidak ada lagi di berkas, termasuk salinan gandanya |

**Selalu tekan “Periksa dulu” lebih dahulu.** Tombol itu membaca semua berkas dan
menampilkan hitungan lengkap per berkas — berapa baru, diperbarui, sudah sama, dibuang —
lalu **membatalkan semuanya**. Bila angkanya sudah sesuai, pilih berkas yang sama dan
tekan **Simpan ke basis data**.

Pengaman yang selalu berlaku:

- bidang yang sudah punya **lokasi penyimpanan, hasil pemeriksaan, atau catatan
  peminjaman tidak pernah dihapus**, sekalipun memakai mode *Ganti isi desa*;
  jumlahnya dilaporkan sebagai "ditahan";
- desa yang kode atau namanya belum terdaftar akan ditolak, kecuali kotak
  **daftarkan desa baru** dicentang;
- bila ada satu berkas yang gagal di tengah jalan, seluruh impor dibatalkan sehingga
  basis data tidak setengah jadi;
- salinan berkas yang diunggah disimpan di folder `unggahan/<tanggal-jam>/`, dan setiap
  impor yang tersimpan tercatat di aktivitas beranda atas nama pengunggahnya.

Pencocokan bidang memakai kunci **desa + tipe hak + nomor hak**, jadi mengunggah berkas
yang sama dua kali tidak menggandakan data.

### Alur harian

1. Masuk dengan akun Anda sendiri. Nama dan peran tampil di kanan atas.
2. **Katalog** → cari nomor hak / NIB / nama pemilik → klik nomor haknya.
3. Di halaman detail:
   - isi **lokasi penyimpanan fisik** (ruang, lemari, rak, box, no. urut);
   - isi **hasil pemeriksaan** buku tanah, surat ukur, dan warkah;
   - klik **Simpan**.
4. Kalau warkahnya dipinjam, isi **Catat peminjaman**. Saat dikembalikan, klik
   **Catat pengembalian** (bisa juga dari menu Sirkulasi).
5. Progres di menu **Monitoring** bergerak sendiri mengikuti pemeriksaan yang tersimpan.

Lama pinjam standar 14 hari — ubah nilai `LAMA_PINJAM_HARI` di `app.py` bila perlu.

### Centang cepat dari katalog

Untuk input massal tanpa membuka satu per satu, gunakan bilah centang di atas tabel
**Katalog**:

1. Saring dulu, misalnya pilih kecamatan lalu desanya.
2. Atur sekali saja di bilah biru: **Petugas**, **Tanggal**, **Tempat** (opsional),
   dan **Status**.
3. Centang kolom **BT** bila buku tanahnya ada dan **SU** bila surat ukurnya ada.
   Kotak di kepala kolom mencentang seluruh baris pada halaman itu sekaligus.
4. Klik **Simpan centang**.

Aturan yang berlaku:

- Hanya baris yang benar-benar Anda ubah yang disimpan — baris lain tidak tersentuh.
  Baris yang akan disimpan disorot kuning.
- Centang = "Ada", tidak dicentang = "Tidak Ada" (hanya untuk baris yang Anda ubah).
- Status **(otomatis)** = *Lengkap* bila BT dan SU sama-sama dicentang, selain itu
  *Tidak Lengkap*. Bisa ditimpa lewat pilihan Status di bilah.
- Kolom pemeriksaan lain — kondisi, kesesuaian, warkah, verifikator, catatan — **tidak
  ikut berubah**. Itu tetap diisi lewat halaman detail bidang.
- Petugas biasa selalu tercatat atas namanya sendiri; hanya Admin yang bisa memilih nama
  petugas lain.

## Membuka dari ponsel

Seluruh halaman menyesuaikan lebar layar — cukup buka `http://<ip-server>:8000`
dari peramban ponsel yang tersambung ke jaringan kantor. Menu, borang, dan tabel
menumpuk ke bawah, tidak ada yang perlu digeser ke samping.

Khusus **Katalog**, di layar sempit hanya ditampilkan keterangan yang dipakai saat
memverifikasi buku tanah dan surat ukur:

| Layar | Yang tampil di tabel katalog |
|---|---|
| Ponsel (≤600 px) | Centang BT & SU, lalu **Nomor Hak**, **Desa**, **SU**, **Pemilik** bertumpuk ke bawah |
| Tablet (≤992 px) | Enam kolom yang sama, berdampingan |
| Laptop / PC | Seluruh kolom seperti biasa |

Kolom Jenis Hak, Kecamatan, NIB, Luas, KW, Lokasi Simpan, dan Status disembunyikan di
layar sempit — semuanya tetap bisa dilihat dengan mengetuk nomor haknya.

Kotak centang diperbesar agar mudah disentuh, dan tombol **Centang semua** di kepala
kolom tetap tersedia.

## Berkas

| Berkas | Isi |
|---|---|
| `app.py` | Aplikasi web (Starlette + Uvicorn) |
| `db.py` | Skema basis data dan daftar nilai dropdown |
| `auth.py` | Penyandian sandi dan peran pengguna |
| `kelola_pengguna.py` | Pengelolaan akun lewat terminal |
| `rahasia.txt` | Kunci penanda tangan cookie sesi — **jangan dihapus atau dibagikan** |
| `import_data.py` | Memuat data dari berkas Excel hasil tarikan KKP |
| `impor.py` | Pembacaan berkas unggahan di balik menu **Impor** |
| `unggahan/` | Salinan berkas yang pernah diunggah lewat menu Impor |
| `warkah.db` | Basis data SQLite — **ini data Anda, cadangkan berkala** |
| `templates/`, `static/` | Tampilan |
| `jalankan.bat` | Penjalan aplikasi |

## Mencadangkan data

Hentikan aplikasi, lalu salin `warkah.db` ke tempat aman. Lakukan rutin — seluruh hasil
inventarisasi, lokasi simpan, dan riwayat peminjaman ada di berkas itu.

## Menambah jenis hak lain

Struktur sudah siap untuk Hak Guna Usaha, HGB, Hak Pakai, Hak Pengelolaan, dan Wakaf —
saat ini baru Hak Milik yang terisi. Untuk menambah:

1. Tarik datanya dari aplikasi KKP dengan cara yang sama seperti Hak Milik
   (`hak=BT2` … `BT8` pada endpoint `GetExportDataDetail`), gabungkan jadi satu
   berkas per jenis hak.
2. Unggah berkasnya lewat menu **Impor** — kolom `Tipe_Hak` yang berisi `BT2` … `BT8`
   sudah dikenali, tidak ada yang perlu diubah di program.

Cara lama lewat terminal juga masih ada: tambahkan berkasnya ke daftar `SUMBER` di
`import_data.py` lalu jalankan `python import_data.py`. Bedanya, `import_data.py`
**menulis ulang seluruh tabel `bidang`**, sedangkan menu Impor hanya menyentuh desa yang
berkasnya diunggah. Keduanya tidak pernah menghapus **lokasi simpan, pemeriksaan,
peminjaman, dan penugasan**.

## Catatan data

- Kode desa tidak selalu unik di data BPN — kode `18040303` dipakai oleh **BUBE** dan
  **DUANO**. Karena itu pencocokan desa memakai pasangan kode + nama, bukan kode saja.
- Nama kecamatan diambil apa adanya dari KKP, termasuk penulisan ganda
  `BONE PANTAI` dan `BONEPANTAI`.
- Desa `Bonebolango` (kode `3005`) dan `TOMBULILATO` belum punya data Hak Milik.

## Keamanan

Aplikasi sudah memakai halaman masuk dan pemisahan peran. Sandi disimpan sebagai hash
PBKDF2-SHA256 (200.000 iterasi, bergaram) — sandi asli tidak pernah tersimpan, jadi
sandi yang lupa harus diatur ulang, bukan dilihat.

Meski begitu aplikasi ini dirancang untuk **jaringan kantor tertutup**: lalu lintasnya
masih HTTP biasa (belum HTTPS), sehingga sandi bisa terbaca oleh siapa pun yang mampu
menyadap jaringan. Data pemilik tanah bersifat pribadi, jadi **jangan membuka port 8000
ke internet**. Bila nanti perlu diakses dari luar kantor, pasang HTTPS lebih dulu.

Cadangkan `rahasia.txt` bersama `warkah.db`. Bila berkas itu hilang, semua sesi yang
sedang berjalan otomatis batal dan semua orang harus masuk ulang (data tidak hilang).
