# -*- coding: utf-8 -*-
"""Impor daftar residu PTSL - sertipikat yang belum diserahkan ke pemohon.

Berkas sumbernya adalah ``RESIDU PTSL.xlsx``: satu buku kerja dengan sepasang
lembar per tahun, ``RESIDU <tahun>`` (rekap per desa, tidak dipakai) dan
``Tipologi <tahun>`` (rincian per sertipikat, inilah yang dibaca).

Kepala tabel lembar Tipologi bertingkat dua sampai tiga baris karena kolom
tipologi permasalahan digabung (T1 membawahi T1.1 .. T1.4, dan seterusnya),
jadi kepala tabel dicari sendiri, bukan lewat ``header=`` bawaan pandas.

Satu baris residu menempel satu-lawan-satu ke tabel ``bidang`` lewat nomor hak.
Baris yang nomor haknya belum ada di basis data tetap disimpan dengan
``bidang_id`` kosong supaya bisa dicocokkan lagi setelah data bidangnya masuk.
"""
import io
import os
import re
from datetime import datetime

import pandas as pd

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FOLDER_UNGGAHAN = os.environ.get("WARKAH_UNGGAHAN") or os.path.join(BASE_DIR, "unggahan")

EKSTENSI = (".xlsx", ".xlsm", ".xls", ".csv")
UKURAN_MAKS = 60 * 1024 * 1024

MODE = {
    "perbarui": "Tambah baris baru dan perbarui yang sudah ada",
    "tambah": "Hanya tambah baris baru, yang sudah ada dibiarkan",
}

# lembar yang dibaca: "Tipologi 2017", " Tipologi 2020", "TIPOLOGI 2025", ...
POLA_LEMBAR = re.compile(r"^\s*tipologi\s*(\d{4})\s*$", re.IGNORECASE)
POLA_TAHUN = re.compile(r"(20\d{2})")

# kolom tujuan -> nama kolom yang mungkin dipakai di berkas sumber
ALIAS = {
    "tahun": ["tahun", "tahun berkas", "tahun anggaran"],
    "nomor_berkas": ["nomor berkas", "no berkas", "nomor_berkas", "no_berkas", "berkas"],
    "desa": ["desa", "desa/kelurahan", "desa/ kelurahan", "desa/kel", "desa/ kel",
             "kelurahan", "nama desa"],
    "kecamatan": ["kecamatan", "kec", "nama kecamatan"],
    "jenis_hak": ["jenis hak", "jenis_hak", "tipe hak"],
    "nomor_hak": ["nomor hak", "no hak", "no. hak", "nomor_hak", "no_hak", "nomor"],
    "nama_pemegang": ["nama", "nama pemegang hak", "nama pemegang", "pemegang hak",
                      "nama pemilik", "pemilik"],
    "no_seri_blanko": ["no seri", "no. seri", "nomor seri", "nomor seri blanko",
                       "no. seri blanko (jika ada)", "no seri blanko"],
    "luas": ["luas", "luas (m2)", "luas m2"],
    "sudah_diserahkan": ["sudah diserahkan", "diserahkan", "status penyerahan"],
    "keterangan": ["keterangan", "ket"],
}

WAJIB = ("nomor_hak",)

# kolom yang ikut diperbarui saat baris residu sudah pernah diimpor; kolom
# kerja (status, tindak_lanjut, petugas, tanggal_serah, penerima, catatan) dan
# hasil cek blanko (blanko_*) tidak pernah ditimpa impor ulang
KOLOM_ISI = ["bidang_id", "wilayah_id", "tahun", "nomor_berkas", "nomor_hak",
             "nomor_hak_asli", "jenis_hak", "jenis_hak_teks", "desa_teks",
             "kecamatan_teks", "nama_pemegang", "no_seri_blanko", "luas",
             "sudah_diserahkan", "tipologi", "keterangan"]

HITUNGAN = ("baris", "baru", "diperbarui", "sama", "cocok", "belum_cocok",
            "lewat", "ganda")

JENIS_DIGIT = {"1": "BT1", "2": "BT2", "3": "BT3", "4": "BT4",
               "5": "BT5", "8": "BT8"}

# nomor hak bentuk KKP baru, mis. 3005.000002708.0 - tidak bisa dipecah jadi
# kode desa + nomor seperti bentuk lama, jadi disimpan apa adanya
POLA_KKP_BARU = re.compile(r"^\d{4}\.\d{6,12}\.\d+$")

# satu nama desa sering dipakai beberapa kode karena basis data memuat kode
# lama dan baru sekaligus; residu PTSL selalu memakai kode yang paling baru
AWALAN_KODE = ("3005", "30")

# nilai sel yang berarti "sudah diserahkan"
YA = {"true", "ya", "sudah", "y", "1", "v", "ok"}


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
    s = re.sub(r"\s+", " ", str(v)).strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    return s


def angka(v):
    """Luas dalam meter persegi; kosong atau bukan angka menjadi None."""
    s = bersih(v)
    if s is None:
        return None
    if s.count(".") > 1:
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
    return re.sub(r"[\s.]+", " ", str(nama).strip().lower()).strip()


def ya_tidak(v) -> int:
    s = bersih(v)
    return 1 if s and s.lower() in YA else 0


def titik(digit14: str) -> str:
    """``30051802100414`` -> ``30.05.18.02.1.00414`` (bentuk baku tabel bidang)."""
    d = digit14
    return "%s.%s.%s.%s.%s.%s" % (d[0:2], d[2:4], d[4:6], d[6:8], d[8:9], d[9:14])


# ------------------------------------------------------------------ wilayah
class Wilayah:
    """Pencarian desa berdasarkan nama, dengan singgahan di memori."""

    def __init__(self, kon):
        self.per_nama = {}          # NAMA DESA -> [(id, kode_desa)]
        self.per_kode = {}          # kode_desa -> id
        for r in kon.execute("SELECT id, kode_desa, nama_desa FROM wilayah"):
            self.per_nama.setdefault(samakan(r["nama_desa"]), []).append(
                (r["id"], r["kode_desa"]))
            self.per_kode.setdefault(r["kode_desa"], r["id"])

    def cari_nama(self, nama):
        """(id_wilayah, kode_desa, catatan) dari nama desa.

        Banyak nama desa dipakai lebih dari sekali karena basis data memuat
        kode lama (18XX) dan kode baru (30XX). Residu PTSL selalu memakai kode
        baru, jadi bila namanya ganda yang dipilih adalah desa berkode 30XX.
        """
        calon = self.per_nama.get(samakan(nama)) or []
        if not calon:
            return None, None, "desa '%s' tidak ada di tabel wilayah" % (nama or "?")
        if len(calon) == 1:
            return calon[0][0], calon[0][1], None
        for awalan in AWALAN_KODE:
            baru = [c for c in calon if c[1].startswith(awalan)]
            if len(baru) == 1:
                return baru[0][0], baru[0][1], None
        return None, None, ("nama desa '%s' dipakai %d desa, tidak bisa dipastikan"
                            % (nama, len(calon)))

    def dari_kode(self, kode_desa):
        return self.per_kode.get(kode_desa)


# --------------------------------------------------------------- nomor hak
def baku_nomor_hak(mentah, desa, wilayah):
    """Nomor hak apa adanya -> bentuk baku tabel bidang.

    Tiga bentuk yang ditemui di berkas:
      * 14 angka penuh, mis. ``30051802100414`` -> ``30.05.18.02.1.00414``
      * nomor pendek 1-5 angka (lembar 2017), kode desanya diambil dari
        kolom Desa, jenis haknya dianggap Hak Milik
      * bentuk KKP baru, mis. ``3005.000002708.0`` -> dipakai apa adanya

    Hasil: (nomor_hak, wilayah_id, jenis_hak, catatan).
    """
    asli = bersih(mentah)
    if not asli:
        return None, None, None, "nomor hak kosong"

    if POLA_KKP_BARU.match(asli):
        return asli, wilayah.cari_nama(desa)[0], None, None

    angka_saja = re.sub(r"[^0-9]", "", asli)

    if len(angka_saja) == 14:
        kode_desa = angka_saja[0:8]
        digit = angka_saja[8:9]
        if angka_saja[8:14] == "000000" or kode_desa.endswith("0000"):
            return asli, None, None, "nomor hak belum lengkap: %s" % asli
        return (titik(angka_saja), wilayah.dari_kode(kode_desa),
                JENIS_DIGIT.get(digit), None)

    if 1 <= len(angka_saja) <= 5 and re.fullmatch(r"[0-9]+", asli.replace(" ", "")):
        wid, kode_desa, catatan = wilayah.cari_nama(desa)
        if not kode_desa:
            return asli, None, None, catatan
        return ("%s.%s.%s.%s.1.%s" % (kode_desa[0:2], kode_desa[2:4], kode_desa[4:6],
                                      kode_desa[6:8], angka_saja.zfill(5)),
                wid, "BT1", None)

    # bentuk lain (KKP baru): simpan apa adanya, desa dicari lewat namanya
    wid, _, _ = wilayah.cari_nama(desa)
    return asli, wid, None, None


def buat_kunci(nomor_hak, tahun, nomor_berkas, asli):
    """Kunci pembeda baris residu supaya impor ulang tidak menggandakan data."""
    if nomor_hak and re.fullmatch(r"\d{2}(\.\d{2}){3}\.\d\.\d{5}", nomor_hak):
        return nomor_hak
    return "%s|%s|%s" % (tahun or "-", nomor_berkas or "-", asli or "-")


# ------------------------------------------------------------------ lembar
def cari_kepala(df):
    """Nomor baris kepala tabel (yang memuat kolom Nomor Hak); None bila tidak ada."""
    for i in range(min(12, len(df))):
        for v in df.iloc[i]:
            if re.fullmatch(r"no\.? ?hak|nomor hak", kunci_kolom(v)):
                return i
    return None


def petakan_kolom(df, kepala):
    """Cocokkan kolom berkas dengan kolom baku; hasil {kolom_baku: nomor_kolom}."""
    tersedia = {}
    for j, v in enumerate(df.iloc[kepala]):
        k = kunci_kolom(v)
        if k and k != "nan":
            tersedia.setdefault(k, j)
    peta = {}
    for tujuan, kandidat in ALIAS.items():
        for k in kandidat:
            j = tersedia.get(kunci_kolom(k))
            if j is not None:
                peta[tujuan] = j
                break
    return peta


def petakan_tipologi(df, kepala):
    """Kolom centang tipologi; hasil {nomor_kolom: kode}.

    Kode paling rinci menang: bila satu kolom bertuliskan ``T1`` di baris atas
    dan ``T1.1`` di baris bawah, yang dipakai ``T1.1``.
    """
    kolom = {}
    for i in range(max(0, kepala - 2), min(len(df), kepala + 3)):
        for j, v in enumerate(df.iloc[i]):
            s = bersih(v)
            if not s:
                continue
            s = s.upper().replace(" ", "")
            if re.fullmatch(r"T\d\.\d", s):
                kolom[j] = s
            elif re.fullmatch(r"T\d", s) and j not in kolom:
                kolom[j] = s
    return kolom


def tahun_lembar(nama_lembar, peta, baris):
    """Tahun residu: dari nama lembar, kalau tidak ada dari kolom Tahun."""
    m = POLA_LEMBAR.match(nama_lembar or "")
    if m:
        return m.group(1)
    m = POLA_TAHUN.search(nama_lembar or "")
    if m:
        return m.group(1)
    if "tahun" in peta:
        for r in baris:
            t = bersih(r[peta["tahun"]])
            if t and POLA_TAHUN.search(t):
                return POLA_TAHUN.search(t).group(1)
    return None


# ------------------------------------------------------------------ hitungan
def rangkuman(berkas=None, lembar=None, tahun=None):
    h = {k: 0 for k in HITUNGAN}
    h.update({"berkas": berkas, "lembar": lembar, "tahun": tahun,
              "status": "ok", "pesan": None, "catatan": []})
    return h


def galat(berkas, pesan, lembar=None, status="galat"):
    h = rangkuman(berkas, lembar)
    h["status"] = status
    h["pesan"] = pesan
    return h


# ------------------------------------------------------------------ proses
def proses_lembar(kon, wilayah, nomor_bidang, df, nama_lembar, opsi, berkas):
    """Baca satu lembar Tipologi lalu tulis isinya ke tabel residu."""
    kepala = cari_kepala(df)
    if kepala is None:
        return galat(berkas, "kolom Nomor Hak tidak ditemukan", nama_lembar)

    peta = petakan_kolom(df, kepala)
    for k in WAJIB:
        if k not in peta:
            return galat(berkas, "kolom %s tidak ditemukan" % k, nama_lembar)
    kol_tipologi = petakan_tipologi(df, kepala)

    baris = [r for _, r in df.iloc[kepala + 1:].iterrows()]
    tahun_baku = tahun_lembar(nama_lembar, peta, baris)
    h = rangkuman(berkas, nama_lembar, tahun_baku)

    def sel(r, nama):
        j = peta.get(nama)
        return bersih(r[j]) if j is not None else None

    isi = {}
    for r in baris:
        asli = sel(r, "nomor_hak")
        # baris nomor kolom ("1", "2", "3", ...) dan baris jumlah ikut terbaca;
        # baris data selalu punya nomor hak minimal 5 angka atau bertitik
        if not asli:
            continue
        if len(re.sub(r"[^0-9]", "", asli)) < 5 and "." not in asli:
            h["lewat"] += 1
            continue
        h["baris"] += 1

        desa = sel(r, "desa")
        nomor_hak, wid, jenis, catatan = baku_nomor_hak(asli, desa, wilayah)
        if catatan:
            h["catatan"].append(catatan)
        if wid is None and desa:
            wid = wilayah.cari_nama(desa)[0]

        tipologi = sorted({kode for j, kode in kol_tipologi.items() if bersih(r[j])},
                          key=lambda k: (len(k), k))

        tahun = sel(r, "tahun")
        n = {
            "bidang_id": nomor_bidang.get(nomor_hak),
            "wilayah_id": wid,
            "tahun": (POLA_TAHUN.search(tahun).group(1)
                      if tahun and POLA_TAHUN.search(tahun) else tahun_baku),
            "nomor_berkas": sel(r, "nomor_berkas"),
            "nomor_hak": nomor_hak,
            "nomor_hak_asli": asli,
            "jenis_hak": jenis,
            "jenis_hak_teks": sel(r, "jenis_hak"),
            "desa_teks": desa,
            "kecamatan_teks": sel(r, "kecamatan"),
            "nama_pemegang": sel(r, "nama_pemegang"),
            "no_seri_blanko": sel(r, "no_seri_blanko"),
            "luas": angka(r[peta["luas"]]) if "luas" in peta else None,
            "sudah_diserahkan": (ya_tidak(r[peta["sudah_diserahkan"]])
                                 if "sudah_diserahkan" in peta else 0),
            "tipologi": ",".join(tipologi) or None,
            "keterangan": sel(r, "keterangan"),
        }

        kunci = buat_kunci(nomor_hak, n["tahun"], n["nomor_berkas"], asli)
        if kunci in isi:
            h["ganda"] += 1
        isi[kunci] = n

    # bidang_id wajib unik: satu bidang hanya boleh punya satu baris residu
    dipakai = {r["bidang_id"]: r["kunci"] for r in kon.execute(
        "SELECT kunci, bidang_id FROM residu WHERE bidang_id IS NOT NULL")}
    lama = {r["kunci"]: r for r in kon.execute(
        "SELECT kunci, %s FROM residu" % ", ".join(KOLOM_ISI))}

    for kunci, n in isi.items():
        bid = n["bidang_id"]
        if bid and dipakai.get(bid) not in (None, kunci):
            h["catatan"].append("nomor hak %s dipakai lebih dari satu baris residu"
                                % n["nomor_hak"])
            n["bidang_id"] = None
        elif bid:
            dipakai[bid] = kunci

        if n["bidang_id"]:
            h["cocok"] += 1
        else:
            h["belum_cocok"] += 1

        ada = lama.get(kunci)
        if ada is None:
            kon.execute(
                "INSERT INTO residu (kunci, sumber, diimpor_pada, %s) "
                "VALUES (?,?,?,%s)" % (", ".join(KOLOM_ISI),
                                       ",".join("?" * len(KOLOM_ISI))),
                [kunci, berkas, opsi["waktu"]] + [n[k] for k in KOLOM_ISI])
            lama[kunci] = n
            h["baru"] += 1
        elif opsi["mode"] == "tambah" or all(ada[k] == n[k] for k in KOLOM_ISI):
            h["sama"] += 1
        else:
            kon.execute(
                "UPDATE residu SET %s, sumber = ?, diimpor_pada = ? WHERE kunci = ?"
                % ", ".join("%s = ?" % k for k in KOLOM_ISI),
                [n[k] for k in KOLOM_ISI] + [berkas, opsi["waktu"], kunci])
            h["diperbarui"] += 1

    h["catatan"] = list(dict.fromkeys(h["catatan"]))[:4]
    return h


def proses_berkas(kon, wilayah, nomor_bidang, nama_berkas, data, opsi):
    """Baca satu berkas unggahan; hasil daftar rangkuman per lembar."""
    ext = os.path.splitext(nama_berkas)[1].lower()
    if ext not in EKSTENSI:
        return [galat(nama_berkas, "jenis berkas %s tidak didukung, pakai .xlsx, "
                                   ".xls, atau .csv" % (ext or "?"))]
    if len(data) > UKURAN_MAKS:
        return [galat(nama_berkas, "ukuran berkas melebihi %d MB"
                      % (UKURAN_MAKS // (1024 * 1024)))]

    if ext == ".csv":
        df = None
        try:
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(data), dtype=str, sep=None,
                                     engine="python", encoding=enc, header=None)
                    break
                except UnicodeDecodeError:
                    continue
        except Exception as e:                                  # noqa: BLE001
            return [galat(nama_berkas, "berkas tidak terbaca: %s" % e)]
        if df is None:
            return [galat(nama_berkas, "penyandian berkas CSV tidak dikenali")]
        return [proses_lembar(kon, wilayah, nomor_bidang, df, nama_berkas,
                              opsi, nama_berkas)]

    try:
        buku = pd.ExcelFile(io.BytesIO(data))
    except Exception as e:                                      # noqa: BLE001
        return [galat(nama_berkas, "berkas tidak terbaca: %s" % e)]

    lembar = [s for s in buku.sheet_names if POLA_LEMBAR.match(s)]
    # berkas yang hanya berisi satu tabel rincian tetap bisa diunggah
    if not lembar:
        lembar = [s for s in buku.sheet_names
                  if cari_kepala(pd.read_excel(buku, s, header=None, dtype=str,
                                               nrows=12)) is not None]
    if not lembar:
        return [galat(nama_berkas, "tidak ada lembar \"Tipologi <tahun>\" yang "
                                   "berisi kolom Nomor Hak; lembar yang ada: %s"
                      % ", ".join(buku.sheet_names[:8]))]

    hasil = []
    for s in lembar:
        try:
            df = pd.read_excel(buku, s, header=None, dtype=str)
        except Exception as e:                                  # noqa: BLE001
            hasil.append(galat(nama_berkas, "lembar tidak terbaca: %s" % e, s))
            continue
        if df.empty:
            hasil.append(galat(nama_berkas, "lembar kosong", s, status="kosong"))
            continue
        hasil.append(proses_lembar(kon, wilayah, nomor_bidang, df, s, opsi,
                                   nama_berkas))
    return hasil


def arsipkan(berkas):
    """Simpan salinan berkas unggahan agar bisa ditelusuri kemudian."""
    folder = os.path.join(FOLDER_UNGGAHAN, "residu",
                          datetime.now().strftime("%Y-%m-%d_%H%M%S"))
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
def cocokkan_ulang(kon):
    """Isi bidang_id baris residu yang tadinya belum ketemu.

    Dipakai setelah data bidang bertambah lewat menu Impor: nomor hak yang
    dulu belum ada di basis data bisa jadi sekarang sudah ada.
    """
    dipakai = {r[0] for r in kon.execute(
        "SELECT bidang_id FROM residu WHERE bidang_id IS NOT NULL")}
    n = 0
    for r in kon.execute("SELECT id, nomor_hak FROM residu WHERE bidang_id IS NULL "
                         "AND nomor_hak IS NOT NULL").fetchall():
        b = kon.execute("SELECT id FROM bidang WHERE nomor_hak = ? ORDER BY id",
                        (r["nomor_hak"],)).fetchone()
        if b and b["id"] not in dipakai:
            kon.execute("UPDATE residu SET bidang_id = ? WHERE id = ?",
                        (b["id"], r["id"]))
            dipakai.add(b["id"])
            n += 1
    return n


def jalankan(berkas, mode="perbarui", simpan=False, arsip=False):
    """Proses semua berkas unggahan.

    ``berkas``  daftar pasangan (nama_berkas, isi_bytes)
    ``mode``    salah satu kunci MODE
    ``simpan``  False berarti uji coba: semua perubahan dibatalkan di akhir
    ``arsip``   simpan salinan berkas ke folder ``unggahan/residu/``
    """
    if mode not in MODE:
        mode = "perbarui"
    opsi = {"mode": mode, "waktu": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    kon = db.sambung()
    rincian = []
    try:
        kon.execute("BEGIN")
        wilayah = Wilayah(kon)
        # nomor hak -> bidang tertua; data lama sempat punya nomor hak ganda
        nomor_bidang = {}
        for r in kon.execute("SELECT id, nomor_hak FROM bidang ORDER BY id DESC"):
            nomor_bidang[r["nomor_hak"]] = r["id"]
        for nama_berkas, data in berkas:
            rincian.extend(proses_berkas(kon, wilayah, nomor_bidang, nama_berkas,
                                         data, opsi))
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
    total["lembar"] = sum(1 for h in rincian if h["status"] == "ok")
    total["galat"] = sum(1 for h in rincian if h["status"] == "galat")

    folder_arsip = None
    if simpan and arsip and berkas:
        try:
            folder_arsip = arsipkan(berkas)
        except OSError as e:
            folder_arsip = "gagal menyimpan salinan: %s" % e

    return {"mode": mode, "simpan": simpan, "total": total, "rincian": rincian,
            "arsip": folder_arsip}


if __name__ == "__main__":
    import sys

    jalur = sys.argv[1] if len(sys.argv) > 1 else "../residu/RESIDU PTSL.xlsx"
    with open(jalur, "rb") as f:
        hasil = jalankan([(os.path.basename(jalur), f.read())],
                         simpan="--simpan" in sys.argv)
    t = hasil["total"]
    print("TERSIMPAN" if hasil["simpan"] else "uji coba (tidak disimpan)")
    print("  baris %d | baru %d | diperbarui %d | sama %d | cocok %d | belum cocok %d"
          % (t["baris"], t["baru"], t["diperbarui"], t["sama"], t["cocok"],
             t["belum_cocok"]))
    for h in hasil["rincian"]:
        print("  %-16s tahun=%-5s baris=%5d cocok=%5d belum=%4d ganda=%d %s"
              % (h["lembar"], h["tahun"], h["baris"], h["cocok"], h["belum_cocok"],
                 h["ganda"], h["pesan"] or ""))
        for c in h["catatan"]:
            print("        - %s" % c)
