"""Impor berkas data bidang lewat halaman web.

Format yang diterima sama dengan berkas di folder ``per_desa``:
berkas Excel/CSV bernama ``HM_<kodedesa>_<NAMA_DESA>.xlsx`` dengan kolom
Nomor_Hak, Surat_Ukur, NIB, Luas, Produk, Luas_Peta, Validator_Tekstual,
Validator_Peta, Blokir_Internal, KW, Pemilik_Pertama, Pemilik_Akhir, Tipe_Hak.

Berkas gabungan yang sudah memuat kolom Kode_Desa dan Nama_Desa juga bisa
dipakai; isinya dipecah per desa secara otomatis.

Modul ini hanya mengurus pembacaan berkas dan penulisan ke tabel wilayah &
bidang. Tabel kerja (penyimpanan, pemeriksaan, peminjaman, penugasan) tidak
pernah disentuh.
"""
import io
import os
import re
from datetime import datetime

import pandas as pd

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# tempat salinan berkas unggahan; di server sebaiknya diarahkan ke volume data
# lewat peubah lingkungan WARKAH_UNGGAHAN agar tidak hilang saat rilis diganti
FOLDER_UNGGAHAN = os.environ.get("WARKAH_UNGGAHAN") or os.path.join(BASE_DIR, "unggahan")

EKSTENSI = (".xlsx", ".xlsm", ".xls", ".csv")
UKURAN_MAKS = 60 * 1024 * 1024          # 60 MB per berkas
POLA_NAMA = re.compile(r"^HM[_-]?(\d{6,10})[_-](.+)$", re.IGNORECASE)

MODE = {
    "perbarui": "Tambah bidang baru dan perbarui yang sudah ada",
    "tambah": "Hanya tambah bidang baru, yang sudah ada dibiarkan",
    "ganti": "Ganti isi desa: tambah, perbarui, lalu buang sisa yang tidak dipakai",
}

# kolom tujuan -> nama kolom yang mungkin dipakai di berkas sumber
ALIAS = {
    "kode_desa": ["kode_desa", "kode desa", "kodedesa", "kode"],
    "nama_desa": ["nama_desa", "nama desa", "desa", "kelurahan"],
    "nomor_hak": ["nomor_hak", "nomor hak", "no_hak", "no hak", "nomorhak", "nomor"],
    "surat_ukur": ["surat_ukur", "surat ukur", "su", "no_su"],
    "nib": ["nib"],
    "luas": ["luas", "luas_tertulis", "luas tertulis"],
    "produk": ["produk", "jenis_produk"],
    "luas_peta": ["luas_peta", "luas peta"],
    "validator_tekstual": ["validator_tekstual", "validator tekstual", "val_tekstual"],
    "validator_peta": ["validator_peta", "validator peta", "val_peta"],
    "blokir_internal": ["blokir_internal", "blokir internal", "blokir"],
    "kw": ["kw", "kualitas", "kw_data"],
    "pemilik_pertama": ["pemilik_pertama", "pemilik pertama", "pemilik_awal"],
    "pemilik_akhir": ["pemilik_akhir", "pemilik akhir", "pemilik", "pemegang_hak"],
    "tipe_hak": ["tipe_hak", "tipe hak", "jenis_hak", "jenis hak", "tipe"],
}

# kolom bidang yang ikut diperbarui (wilayah, jenis_hak, nomor_hak jadi kunci)
KOLOM_ISI = ["surat_ukur", "nib", "luas", "produk", "luas_peta",
             "validator_tekstual", "validator_peta", "blokir_internal",
             "kw", "pemilik_pertama", "pemilik_akhir"]

HITUNGAN = ("baris", "baru", "diperbarui", "sama", "dibuang", "ditahan",
            "lewat", "ganda", "salinan")

KELOMPOK = {"18": "18XX", "30": "30XX"}


# ------------------------------------------------------------------ bantuan
def bersih(v):
    """Nilai sel menjadi teks rapi; sel kosong menjadi None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    return s


def angka(v):
    """Luas dalam meter persegi; kosong atau bukan angka menjadi None."""
    s = bersih(v)
    if s is None:
        return None
    if s.count(".") > 1:            # 1.234.567 -> pemisah ribuan
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return int(float(s))
    except ValueError:
        return None


def samakan(nama) -> str:
    """Nama desa untuk pembanding: huruf besar, spasi tunggal."""
    return re.sub(r"[\s_]+", " ", str(nama or "").strip()).upper()


def kunci_kolom(nama) -> str:
    return re.sub(r"[\s.]+", "_", str(nama).strip().lower()).strip("_")


def pecah_nama_berkas(nama_berkas: str):
    """``HM_18040101_HUNTU.xlsx`` -> ('18040101', 'HUNTU'); gagal -> (None, None)."""
    dasar = os.path.splitext(os.path.basename(nama_berkas))[0]
    m = POLA_NAMA.match(dasar.strip())
    if not m:
        return None, None
    return m.group(1), samakan(m.group(2))


def baca_tabel(nama_berkas: str, data: bytes) -> pd.DataFrame:
    """Baca isi berkas unggahan menjadi tabel teks."""
    ext = os.path.splitext(nama_berkas)[1].lower()
    if ext == ".csv":
        for enc in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(data), dtype=str, sep=None,
                                   engine="python", encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError("penyandian berkas CSV tidak dikenali")
    return pd.read_excel(io.BytesIO(data), dtype=str)


def petakan_kolom(df: pd.DataFrame) -> dict:
    """Cocokkan kolom berkas dengan kolom baku; hasil {kolom_baku: nama_asli}."""
    tersedia = {}
    for asli in df.columns:
        tersedia.setdefault(kunci_kolom(asli), asli)
    peta = {}
    for tujuan, kandidat in ALIAS.items():
        for k in kandidat:
            asli = tersedia.get(kunci_kolom(k))
            if asli is not None:
                peta[tujuan] = asli
                break
    return peta


# ------------------------------------------------------------------ wilayah
class Wilayah:
    """Pencarian dan pendaftaran desa, dengan singgahan di memori."""

    def __init__(self, kon):
        self.kon = kon
        self.per_pasangan = {}
        self.per_kode = {}
        self.nama = {}
        self.kecamatan = {}
        for r in kon.execute("SELECT id, kode_desa, nama_desa, nama_kecamatan, "
                             "kode_kec FROM wilayah"):
            self._ingat(r["id"], r["kode_desa"], r["nama_desa"],
                        r["nama_kecamatan"], r["kode_kec"])

    def _ingat(self, wid, kode, nama, kecamatan, kode_kec):
        self.per_pasangan[(kode, samakan(nama))] = wid
        self.per_kode.setdefault(kode, []).append(wid)
        self.nama[wid] = nama
        if kode_kec and kecamatan:
            self.kecamatan.setdefault(kode_kec, kecamatan)

    def cari(self, kode, nama):
        """(id_wilayah, catatan); id None bila desa belum terdaftar."""
        wid = self.per_pasangan.get((kode, samakan(nama)))
        if wid:
            return wid, None
        sama_kode = self.per_kode.get(kode) or []
        if len(sama_kode) == 1:
            wid = sama_kode[0]
            return wid, ("dicocokkan lewat kode desa: di basis data tertulis '%s', "
                         "di berkas '%s'" % (self.nama[wid], nama))
        if len(sama_kode) > 1:
            return None, ("kode %s dipakai %d desa dan tidak ada yang bernama '%s'"
                          % (kode, len(sama_kode), nama))
        return None, None

    def buat(self, kode, nama):
        """Daftarkan desa baru; kecamatan diambil dari desa lain sekecamatan."""
        kode_kec = kode[:6] if len(kode) == 8 else kode
        kecamatan = self.kecamatan.get(kode_kec) or "(Belum Ditetapkan)"
        kelompok = KELOMPOK.get(kode[:2], kode[:2])
        cur = self.kon.execute(
            "INSERT INTO wilayah (kelompok, kode_kec, nama_kecamatan, kode_desa, "
            "nama_desa) VALUES (?,?,?,?,?)",
            (kelompok, kode_kec, kecamatan, kode, nama))
        wid = cur.lastrowid
        self._ingat(wid, kode, nama, kecamatan, kode_kec)
        return wid


# ------------------------------------------------------------------ per desa
def rangkuman(berkas=None, kode=None, nama=None):
    h = {k: 0 for k in HITUNGAN}
    h.update({"berkas": berkas, "kode_desa": kode, "nama_desa": nama,
              "desa_baru": False, "status": "ok", "pesan": None, "catatan": []})
    return h


def galat(berkas, pesan, status="galat"):
    h = rangkuman(berkas)
    h["status"] = status
    h["pesan"] = pesan
    return h


def nilai_baris(r, peta):
    """Satu baris berkas menjadi kamus nilai bidang."""
    def ambil(k):
        return bersih(r.get(peta[k])) if k in peta else None

    nilai = {k: ambil(k) for k in KOLOM_ISI}
    nilai["luas"] = angka(r.get(peta["luas"])) if "luas" in peta else None
    nilai["nomor_hak"] = ambil("nomor_hak")
    nilai["jenis_hak"] = (ambil("tipe_hak") or "BT1").upper()
    return nilai


def proses_desa(kon, wilayah, kode_hak, kode, nama, baris, opsi, berkas):
    """Tulis satu kelompok baris (satu desa) ke tabel bidang."""
    h = rangkuman(berkas, kode, nama)
    h["baris"] = len(baris)

    wid, catatan = wilayah.cari(kode, nama)
    if catatan:
        h["catatan"].append(catatan)
    if wid is None:
        if not opsi["buat_desa"]:
            h["status"] = "galat"
            h["pesan"] = ("desa %s %s belum terdaftar - centang \"daftarkan desa "
                          "baru\" bila memang desa baru" % (kode, nama))
            return h
        wid = wilayah.buat(kode, nama)
        h["desa_baru"] = True

    # baris berkas; nomor hak ganda di dalam satu berkas dimenangkan yang terakhir
    isi = {}
    for r in baris:
        n = nilai_baris(r, opsi["peta"])
        if not n["nomor_hak"]:
            h["lewat"] += 1
            continue
        if n["jenis_hak"] not in kode_hak:
            h["lewat"] += 1
            h["catatan"].append("tipe hak '%s' tidak dikenal" % n["jenis_hak"])
            continue
        kunci = (n["jenis_hak"], n["nomor_hak"])
        if kunci in isi:
            h["ganda"] += 1
        isi[kunci] = n
    h["catatan"] = list(dict.fromkeys(h["catatan"]))[:3]

    # bidang yang sudah ada; satu nomor hak bisa punya lebih dari satu baris
    # (data lama pernah dimuat tanpa penyaringan), yang tertua jadi acuan
    lama = {}
    for r in kon.execute(
            "SELECT id, jenis_hak, nomor_hak, %s FROM bidang WHERE wilayah_id = ? "
            "ORDER BY id" % ", ".join(KOLOM_ISI), (wid,)):
        lama.setdefault((r["jenis_hak"], r["nomor_hak"]), []).append(r)
    h["salinan"] = sum(len(v) - 1 for v in lama.values())

    tambah, ubah = [], []
    for kunci, n in isi.items():
        daftar = lama.get(kunci)
        if not daftar:
            tambah.append((wid, n["jenis_hak"], n["nomor_hak"])
                          + tuple(n[k] for k in KOLOM_ISI))
            continue
        ada = daftar[0]
        if opsi["mode"] == "tambah" or all(ada[k] == n[k] for k in KOLOM_ISI):
            h["sama"] += 1
        else:
            ubah.append(tuple(n[k] for k in KOLOM_ISI) + (ada["id"],))

    if tambah:
        kon.executemany(
            "INSERT INTO bidang (wilayah_id, jenis_hak, nomor_hak, %s) VALUES (%s)"
            % (", ".join(KOLOM_ISI), ",".join("?" * (3 + len(KOLOM_ISI)))), tambah)
        h["baru"] = len(tambah)
    if ubah:
        kon.executemany(
            "UPDATE bidang SET %s WHERE id = ?"
            % ", ".join("%s = ?" % k for k in KOLOM_ISI), ubah)
        h["diperbarui"] = len(ubah)

    if opsi["mode"] == "ganti":
        h["dibuang"], h["ditahan"] = buang_sisa(kon, lama, isi)
    return h


def buang_sisa(kon, lama, isi):
    """Hapus bidang lama yang tidak ada lagi di berkas, berikut salinan gandanya.

    Bidang yang sudah punya catatan kerja (penyimpanan, pemeriksaan,
    peminjaman, atau residu) tidak pernah dihapus supaya hasil inventarisasi
    tidak ikut hilang; jumlahnya dilaporkan sebagai "ditahan".
    """
    sisa = []
    for kunci, daftar in lama.items():
        # nomor hak yang masih dipakai hanya menyisakan baris tertua
        sisa.extend(r["id"] for r in (daftar[1:] if kunci in isi else daftar))
    if not sisa:
        return 0, 0
    dipakai = set()
    for tabel in ("penyimpanan", "pemeriksaan", "peminjaman", "residu"):
        for potong in (sisa[i:i + 400] for i in range(0, len(sisa), 400)):
            dipakai.update(r[0] for r in kon.execute(
                "SELECT bidang_id FROM %s WHERE bidang_id IN (%s)"
                % (tabel, ",".join("?" * len(potong))), potong))
    aman = [i for i in sisa if i not in dipakai]
    for potong in (aman[i:i + 400] for i in range(0, len(aman), 400)):
        kon.execute("DELETE FROM bidang WHERE id IN (%s)"
                    % ",".join("?" * len(potong)), potong)
    return len(aman), len(sisa) - len(aman)


# ------------------------------------------------------------------ per berkas
def proses_berkas(kon, wilayah, kode_hak, nama_berkas, data, mode, buat_desa):
    """Baca satu berkas unggahan lalu simpan isinya; hasil daftar rangkuman."""
    ext = os.path.splitext(nama_berkas)[1].lower()
    if ext not in EKSTENSI:
        return [galat(nama_berkas, "jenis berkas %s tidak didukung, pakai .xlsx, "
                                   ".xls, atau .csv" % (ext or "?"))]
    if len(data) > UKURAN_MAKS:
        return [galat(nama_berkas, "ukuran berkas melebihi %d MB"
                      % (UKURAN_MAKS // (1024 * 1024)))]
    try:
        df = baca_tabel(nama_berkas, data)
    except Exception as e:                                      # noqa: BLE001
        return [galat(nama_berkas, "berkas tidak terbaca: %s" % e)]
    if df.empty:
        return [galat(nama_berkas, "berkas tidak berisi data", status="kosong")]

    peta = petakan_kolom(df)
    if "nomor_hak" not in peta:
        return [galat(nama_berkas, "kolom Nomor_Hak tidak ditemukan; kolom yang "
                                   "terbaca: %s"
                      % ", ".join(str(c) for c in df.columns[:8]))]

    opsi = {"mode": mode, "buat_desa": buat_desa, "peta": peta}
    baris = df.to_dict("records")

    # desa bisa berasal dari kolom (berkas gabungan) atau dari nama berkas
    if "kode_desa" in peta and "nama_desa" in peta:
        kelompok, tanpa_desa = {}, 0
        for r in baris:
            kode = bersih(r.get(peta["kode_desa"]))
            nama = samakan(bersih(r.get(peta["nama_desa"])))
            if not kode or not nama:
                tanpa_desa += 1
                continue
            kelompok.setdefault((kode, nama), []).append(r)
        if not kelompok:
            return [galat(nama_berkas, "kolom Kode_Desa/Nama_Desa kosong semua")]
        hasil = [proses_desa(kon, wilayah, kode_hak, kode, nama, br, opsi, nama_berkas)
                 for (kode, nama), br in sorted(kelompok.items())]
        if tanpa_desa:
            hasil[0]["lewat"] += tanpa_desa
            hasil[0]["catatan"].append("%d baris tanpa kode/nama desa" % tanpa_desa)
        return hasil

    kode, nama = pecah_nama_berkas(nama_berkas)
    if not kode:
        return [galat(nama_berkas, "nama berkas tidak sesuai pola "
                                   "HM_<kodedesa>_<NAMA_DESA> dan berkas tidak "
                                   "punya kolom Kode_Desa/Nama_Desa")]
    return [proses_desa(kon, wilayah, kode_hak, kode, nama, baris, opsi, nama_berkas)]


def arsipkan(berkas):
    """Simpan salinan berkas unggahan agar bisa ditelusuri kemudian."""
    folder = os.path.join(FOLDER_UNGGAHAN, datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    for nama_berkas, data in berkas:
        aman = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(nama_berkas))[:120]
        tujuan = os.path.join(folder, aman or "berkas")
        n = 1
        while os.path.exists(tujuan):
            akar, ext = os.path.splitext(tujuan)
            tujuan, n = "%s(%d)%s" % (akar, n, ext), n + 1
        with open(tujuan, "wb") as f:
            f.write(data)
    return folder


# ------------------------------------------------------------------ utama
def jalankan(berkas, mode="perbarui", buat_desa=False, simpan=False, arsip=False):
    """Proses semua berkas unggahan.

    ``berkas``   daftar pasangan (nama_berkas, isi_bytes)
    ``mode``     salah satu kunci MODE
    ``buat_desa``daftarkan desa yang belum ada di tabel wilayah
    ``simpan``   False berarti uji coba: semua perubahan dibatalkan di akhir
    ``arsip``    simpan salinan berkas ke folder ``unggahan/``
    """
    if mode not in MODE:
        mode = "perbarui"
    kon = db.sambung()
    rincian = []
    try:
        kon.execute("BEGIN")
        kode_hak = {r["kode"] for r in kon.execute("SELECT kode FROM jenis_hak")}
        wilayah = Wilayah(kon)
        for nama_berkas, data in berkas:
            rincian.extend(proses_berkas(kon, wilayah, kode_hak, nama_berkas,
                                         data, mode, buat_desa))
        if simpan:
            kon.commit()
        else:
            kon.rollback()
    except Exception:                                           # noqa: BLE001
        kon.rollback()
        raise
    finally:
        kon.close()

    total = {k: sum(h[k] for h in rincian) for k in HITUNGAN}
    total["berkas"] = len(berkas)
    total["desa"] = len({(h["kode_desa"], h["nama_desa"]) for h in rincian
                         if h["kode_desa"]})
    total["desa_baru"] = sum(1 for h in rincian if h["desa_baru"])
    total["galat"] = sum(1 for h in rincian if h["status"] == "galat")

    folder_arsip = None
    if simpan and arsip and berkas:
        try:
            folder_arsip = arsipkan(berkas)
        except OSError as e:
            folder_arsip = "gagal menyimpan salinan: %s" % e

    return {"mode": mode, "simpan": simpan, "total": total, "rincian": rincian,
            "arsip": folder_arsip}
