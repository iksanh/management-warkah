"""Koneksi dan skema basis data Aplikasi Warkah."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "warkah.db")

JENIS_HAK = [
    ("BT1", "Hak Milik"),
    ("BT2", "Hak Guna Usaha"),
    ("BT3", "Hak Guna Bangunan"),
    ("BT4", "Hak Pakai"),
    ("BT5", "Hak Pengelolaan"),
    ("BT8", "Hak Wakaf"),
]

PETUGAS_AWAL = ["Iman", "Rian", "Mimin", "Bento"]

# tipologi permasalahan residu PTSL; kolom "nama" diisi/diubah lewat halaman
# Residu karena keterangan resminya tidak ikut tertulis di berkas Excel
TIPOLOGI = [
    ("T1.1", "T1", 1), ("T1.2", "T1", 2), ("T1.3", "T1", 3), ("T1.4", "T1", 4),
    ("T2.1", "T2", 5), ("T2.2", "T2", 6), ("T2.3", "T2", 7), ("T2.4", "T2", 8),
    ("T3.1", "T3", 9), ("T3.2", "T3", 10), ("T3.3", "T3", 11), ("T3.4", "T3", 12),
    ("T4", "T4", 13), ("T5", "T5", 14), ("T6", "T6", 15), ("T7", "T7", 16),
    ("T8", "T8", 17),
]

SKEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS wilayah (
    id              INTEGER PRIMARY KEY,
    kelompok        TEXT NOT NULL,
    kode_kec        TEXT NOT NULL,
    nama_kecamatan  TEXT NOT NULL,
    kode_desa       TEXT NOT NULL,
    nama_desa       TEXT NOT NULL,
    UNIQUE (kode_desa, nama_desa)
);
CREATE INDEX IF NOT EXISTS ix_wilayah_kec ON wilayah (nama_kecamatan);

CREATE TABLE IF NOT EXISTS jenis_hak (
    kode  TEXT PRIMARY KEY,
    nama  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS petugas (
    id                INTEGER PRIMARY KEY,
    nama              TEXT NOT NULL UNIQUE,
    username          TEXT UNIQUE,
    sandi             TEXT,
    peran             TEXT NOT NULL DEFAULT 'Petugas',
    aktif             INTEGER NOT NULL DEFAULT 1,
    dibuat_pada       TEXT,
    sandi_diubah_pada TEXT,
    terakhir_masuk    TEXT
);

-- satu baris = satu bidang / satu buku tanah (koleksi perpustakaan)
CREATE TABLE IF NOT EXISTS bidang (
    id                  INTEGER PRIMARY KEY,
    wilayah_id          INTEGER NOT NULL REFERENCES wilayah (id),
    jenis_hak           TEXT NOT NULL REFERENCES jenis_hak (kode),
    nomor_hak           TEXT NOT NULL,
    surat_ukur          TEXT,
    nib                 TEXT,
    luas                INTEGER,
    produk              TEXT,
    luas_peta           TEXT,
    validator_tekstual  TEXT,
    validator_peta      TEXT,
    blokir_internal     TEXT,
    kw                  TEXT,
    pemilik_pertama     TEXT,
    pemilik_akhir       TEXT
);
CREATE INDEX IF NOT EXISTS ix_bidang_nomor   ON bidang (nomor_hak);
CREATE INDEX IF NOT EXISTS ix_bidang_wilayah ON bidang (wilayah_id);
CREATE INDEX IF NOT EXISTS ix_bidang_jenis   ON bidang (jenis_hak);
CREATE INDEX IF NOT EXISTS ix_bidang_kw      ON bidang (kw);
CREATE INDEX IF NOT EXISTS ix_bidang_nib     ON bidang (nib);
CREATE INDEX IF NOT EXISTS ix_bidang_pemilik ON bidang (pemilik_akhir);
CREATE INDEX IF NOT EXISTS ix_bidang_su      ON bidang (surat_ukur);

-- "nomor panggil" perpustakaan: di mana warkah disimpan
CREATE TABLE IF NOT EXISTS penyimpanan (
    bidang_id   INTEGER PRIMARY KEY REFERENCES bidang (id),
    ruang       TEXT,
    lemari      TEXT,
    rak         TEXT,
    box         TEXT,
    no_urut     TEXT,
    catatan     TEXT,
    diubah_oleh TEXT,
    diubah_pada TEXT
);
CREATE INDEX IF NOT EXISTS ix_simpan_box ON penyimpanan (ruang, lemari, rak, box);

-- hasil inventarisasi / identifikasi per bidang
CREATE TABLE IF NOT EXISTS pemeriksaan (
    bidang_id           INTEGER PRIMARY KEY REFERENCES bidang (id),
    bt_ada              TEXT,
    bt_kondisi          TEXT,
    bt_sesuai           TEXT,
    su_ada              TEXT,
    su_kondisi          TEXT,
    su_sesuai           TEXT,
    warkah_ada          TEXT,
    warkah_no_berkas    TEXT,
    status_identifikasi TEXT,
    tindak_lanjut       TEXT,
    tempat              TEXT,
    tanggal             TEXT,
    petugas             TEXT,
    verifikator         TEXT,
    tanggal_verifikasi  TEXT,
    catatan             TEXT,
    diubah_pada         TEXT
);
CREATE INDEX IF NOT EXISTS ix_periksa_status  ON pemeriksaan (status_identifikasi);
CREATE INDEX IF NOT EXISTS ix_periksa_petugas ON pemeriksaan (petugas);

-- sirkulasi: peminjaman & pengembalian warkah
CREATE TABLE IF NOT EXISTS peminjaman (
    id               INTEGER PRIMARY KEY,
    bidang_id        INTEGER NOT NULL REFERENCES bidang (id),
    peminjam         TEXT NOT NULL,
    unit             TEXT,
    keperluan        TEXT,
    petugas_pinjam   TEXT,
    tanggal_pinjam   TEXT NOT NULL,
    jatuh_tempo      TEXT,
    tanggal_kembali  TEXT,
    petugas_kembali  TEXT,
    catatan          TEXT
);
CREATE INDEX IF NOT EXISTS ix_pinjam_bidang ON peminjaman (bidang_id);
CREATE INDEX IF NOT EXISTS ix_pinjam_aktif  ON peminjaman (tanggal_kembali);

-- penugasan inventarisasi per desa (dari file monitoring)
CREATE TABLE IF NOT EXISTS penugasan (
    wilayah_id     INTEGER PRIMARY KEY REFERENCES wilayah (id),
    petugas        TEXT,
    tanggal_mulai  TEXT,
    target_selesai TEXT,
    tanggal_selesai TEXT,
    status         TEXT,
    kendala        TEXT,
    catatan        TEXT
);

-- angka pembanding dari aplikasi KKP, diisi manual per desa pada halaman Rekap
CREATE TABLE IF NOT EXISTS rekap_kkp (
    wilayah_id  INTEGER PRIMARY KEY REFERENCES wilayah (id),
    jumlah      INTEGER,
    catatan     TEXT,
    diubah_oleh TEXT,
    diubah_pada TEXT
);

-- acuan kode tipologi permasalahan residu PTSL
CREATE TABLE IF NOT EXISTS tipologi (
    kode      TEXT PRIMARY KEY,
    kelompok  TEXT NOT NULL,
    nama      TEXT,
    urut      INTEGER
);

-- satu baris = satu sertipikat PTSL yang belum diserahkan ke pemohon.
-- Bila nomor haknya ada di tabel bidang, baris ini menempel satu-lawan-satu
-- lewat bidang_id; bila belum ketemu, bidang_id dibiarkan kosong dan barisnya
-- tetap tersimpan supaya bisa dicocokkan lagi kemudian.
CREATE TABLE IF NOT EXISTS residu (
    id               INTEGER PRIMARY KEY,
    kunci            TEXT NOT NULL UNIQUE,
    bidang_id        INTEGER UNIQUE REFERENCES bidang (id),
    wilayah_id       INTEGER REFERENCES wilayah (id),
    tahun            TEXT,
    nomor_berkas     TEXT,
    nomor_hak        TEXT,
    nomor_hak_asli   TEXT,
    jenis_hak        TEXT,
    jenis_hak_teks   TEXT,
    desa_teks        TEXT,
    kecamatan_teks   TEXT,
    nama_pemegang    TEXT,
    no_seri_blanko   TEXT,
    luas             INTEGER,
    sudah_diserahkan INTEGER NOT NULL DEFAULT 0,
    tipologi         TEXT,
    keterangan       TEXT,
    blanko_ada       TEXT,
    blanko_petugas   TEXT,
    blanko_tanggal   TEXT,
    blanko_tempat    TEXT,
    blanko_diubah    TEXT,
    status           TEXT,
    tindak_lanjut    TEXT,
    petugas          TEXT,
    tanggal_serah    TEXT,
    penerima         TEXT,
    catatan          TEXT,
    sumber           TEXT,
    diimpor_pada     TEXT,
    diubah_oleh      TEXT,
    diubah_pada      TEXT
);
CREATE INDEX IF NOT EXISTS ix_residu_nomor   ON residu (nomor_hak);
CREATE INDEX IF NOT EXISTS ix_residu_tahun   ON residu (tahun);
CREATE INDEX IF NOT EXISTS ix_residu_wilayah ON residu (wilayah_id);
CREATE INDEX IF NOT EXISTS ix_residu_status  ON residu (status);
CREATE INDEX IF NOT EXISTS ix_residu_serah   ON residu (sudah_diserahkan);
-- ix_residu_blanko dibuat di migrasi(), sesudah kolomnya dipastikan ada

-- catatan aktivitas ringkas
CREATE TABLE IF NOT EXISTS log_aktivitas (
    id        INTEGER PRIMARY KEY,
    waktu     TEXT NOT NULL,
    petugas   TEXT,
    aksi      TEXT NOT NULL,
    bidang_id INTEGER,
    rincian   TEXT
);
CREATE INDEX IF NOT EXISTS ix_log_waktu ON log_aktivitas (waktu DESC);
"""

# daftar nilai baku untuk dropdown di aplikasi
PILIHAN = {
    "ada": ["Ada", "Tidak Ada", "Tidak Ditemukan", "Dipinjam", "Rusak/Hilang"],
    "kondisi": ["Baik", "Rusak Ringan", "Rusak Berat", "Lapuk/Sobek", "Tulisan Pudar"],
    "sesuai": ["Sesuai", "Tidak Sesuai", "Sebagian Sesuai", "Belum Dicek"],
    "status_identifikasi": ["Lengkap", "Tidak Lengkap", "Perlu Perbaikan Data",
                            "Perlu Scan Ulang", "Data Ganda", "Sudah Mati/Hapus",
                            "Belum Diperiksa"],
    "tindak_lanjut": ["Tidak Perlu", "Perbaikan Tekstual", "Perbaikan Spasial",
                      "Scan/Upload Dokumen", "Pengukuran Ulang", "Telusur Warkah",
                      "Lapor Koordinator"],
    "status_penugasan": ["Belum Mulai", "Proses", "Selesai Periksa", "Verifikasi",
                         "Revisi", "Tertunda"],
    "blanko": ["Ada", "Tidak Ada"],
    "status_residu": ["Belum Ditindaklanjuti", "Dalam Proses", "Siap Diserahkan",
                      "Sudah Diserahkan", "Batal/Dibatalkan", "Tidak Dapat Diselesaikan"],
    "tindak_lanjut_residu": ["Belum Ditentukan", "Panggil Pemohon", "Lengkapi Berkas",
                             "Perbaikan Data", "Ukur/Peta Ulang", "Koordinasi Desa",
                             "Serahkan Lewat Desa", "Ke Seksi Sengketa"],
}


def sambung():
    """Koneksi SQLite siap pakai."""
    kon = sqlite3.connect(DB_PATH, timeout=30)
    kon.row_factory = sqlite3.Row
    kon.execute("PRAGMA foreign_keys = ON")
    return kon


# kolom yang ditambahkan setelah basis data pertama kali dibuat
TAMBAHAN_KOLOM = {
    "residu": [
        ("blanko_ada", "TEXT"),
        ("blanko_petugas", "TEXT"),
        ("blanko_tanggal", "TEXT"),
        ("blanko_tempat", "TEXT"),
        ("blanko_diubah", "TEXT"),
    ],
    "petugas": [
        ("username", "TEXT"),
        ("sandi", "TEXT"),
        ("dibuat_pada", "TEXT"),
        ("sandi_diubah_pada", "TEXT"),
        ("terakhir_masuk", "TEXT"),
    ],
}


def migrasi(kon):
    """Tambahkan kolom baru pada basis data yang sudah terlanjur dibuat."""
    for tabel, kolom in TAMBAHAN_KOLOM.items():
        ada = {r["name"] for r in kon.execute("PRAGMA table_info(%s)" % tabel)}
        for nama, tipe in kolom:
            if nama not in ada:
                kon.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabel, nama, tipe))
    kon.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_petugas_username "
                "ON petugas (username) WHERE username IS NOT NULL")
    kon.execute("CREATE INDEX IF NOT EXISTS ix_residu_blanko ON residu (blanko_ada)")
    kon.commit()


def siapkan():
    """Buat tabel bila belum ada, lalu isi tabel acuan."""
    kon = sambung()
    kon.executescript(SKEMA)
    migrasi(kon)
    kon.executemany("INSERT OR IGNORE INTO jenis_hak (kode, nama) VALUES (?, ?)", JENIS_HAK)
    kon.executemany("INSERT OR IGNORE INTO petugas (nama) VALUES (?)",
                    [(n,) for n in PETUGAS_AWAL])
    kon.executemany("INSERT OR IGNORE INTO tipologi (kode, kelompok, urut) "
                    "VALUES (?,?,?)", TIPOLOGI)
    kon.commit()
    return kon


if __name__ == "__main__":
    kon = siapkan()
    print("basis data siap:", DB_PATH)
    for t in ("wilayah", "bidang", "penyimpanan", "pemeriksaan", "peminjaman",
              "penugasan", "petugas", "jenis_hak", "rekap_kkp", "residu",
              "tipologi"):
        n = kon.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("  %-14s %8d baris" % (t, n))
    kon.close()
