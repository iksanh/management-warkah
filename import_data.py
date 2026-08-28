"""Muat data hasil tarikan aplikasi KKP (xlsx/csv) ke dalam warkah.db.

Jalankan ulang kapan saja: data bidang & wilayah ditulis ulang, sedangkan
tabel kerja (penyimpanan, pemeriksaan, peminjaman, penugasan) tidak disentuh.
"""
import os
import sys

import pandas as pd

import db

DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (berkas gabungan, kode jenis hak)
SUMBER = [
    ("Gabungan_Hak_Milik_18XX.xlsx", "BT1"),
    ("Gabungan_Hak_Milik_30XX.xlsx", "BT1"),
]

KOLOM_BIDANG = ["surat_ukur", "nib", "luas", "produk", "luas_peta",
                "validator_tekstual", "validator_peta", "blokir_internal",
                "kw", "pemilik_pertama", "pemilik_akhir"]


def bersih(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def muat_wilayah(kon):
    peta = pd.read_csv(os.path.join(DATA_DIR, "wilayah_map.csv"), sep=";",
                       dtype=str, encoding="utf-8-sig")
    peta = peta.rename(columns={"KODE": "kode_desa", "NAMA_DESA": "nama_desa",
                                "NAMA_KECAMATAN": "nama_kecamatan"})
    peta["kelompok"] = peta["kode_desa"].str[:2].map({"18": "18XX", "30": "30XX"})
    peta["kode_kec"] = peta["kode_desa"].apply(lambda k: k[:6] if len(k) == 8 else k)
    kon.executemany(
        "INSERT INTO wilayah (kelompok, kode_kec, nama_kecamatan, kode_desa, nama_desa) "
        "VALUES (?,?,?,?,?) ON CONFLICT (kode_desa, nama_desa) DO UPDATE SET "
        "kelompok=excluded.kelompok, kode_kec=excluded.kode_kec, "
        "nama_kecamatan=excluded.nama_kecamatan",
        peta[["kelompok", "kode_kec", "nama_kecamatan", "kode_desa", "nama_desa"]]
        .itertuples(index=False, name=None))
    kon.commit()
    return len(peta)


def muat_bidang(kon):
    # kode desa tidak selalu unik (mis. 18040303 dipakai BUBE dan DUANO),
    # jadi kunci pencocokan adalah pasangan kode + nama desa
    wil = {(r["kode_desa"], r["nama_desa"]): r["id"]
           for r in kon.execute("SELECT id, kode_desa, nama_desa FROM wilayah")}

    total, tanpa_wilayah = 0, set()
    kon.execute("DELETE FROM bidang")
    for berkas, kode_hak in SUMBER:
        path = os.path.join(DATA_DIR, berkas)
        if not os.path.exists(path):
            print("  ! lewati (tidak ada):", berkas)
            continue
        df = pd.read_excel(path, dtype=str)
        baris = []
        for r in df.itertuples(index=False):
            kunci = (bersih(r.Kode_Desa), bersih(r.Nama_Desa))
            wid = wil.get(kunci)
            if wid is None:
                tanpa_wilayah.add(kunci)
                continue
            luas = bersih(r.Luas)
            try:
                luas = int(float(luas)) if luas else None
            except ValueError:
                luas = None
            baris.append((wid, bersih(r.Tipe_Hak) or kode_hak, bersih(r.Nomor_Hak),
                          bersih(r.Surat_Ukur), bersih(r.NIB), luas, bersih(r.Produk),
                          bersih(r.Luas_Peta), bersih(r.Validator_Tekstual),
                          bersih(r.Validator_Peta), bersih(r.Blokir_Internal),
                          bersih(r.KW), bersih(r.Pemilik_Pertama), bersih(r.Pemilik_Akhir)))
        kon.executemany(
            "INSERT INTO bidang (wilayah_id, jenis_hak, nomor_hak, surat_ukur, nib, luas, "
            "produk, luas_peta, validator_tekstual, validator_peta, blokir_internal, kw, "
            "pemilik_pertama, pemilik_akhir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", baris)
        kon.commit()
        total += len(baris)
        print("  %-38s %7d baris" % (berkas, len(baris)))

    if tanpa_wilayah:
        print("  ! %d pasangan kode+nama desa tidak dikenal:" % len(tanpa_wilayah),
              sorted(tanpa_wilayah)[:5])
    return total


def main():
    kon = db.siapkan()
    print("basis data:", db.DB_PATH)
    print("wilayah  :", muat_wilayah(kon), "desa")
    print("bidang   :")
    total = muat_bidang(kon)

    kon.execute("ANALYZE")
    kon.commit()

    print()
    print("RINGKASAN")
    print("  total bidang  :", total)
    for r in kon.execute(
            "SELECT j.nama, COUNT(*) n FROM bidang b JOIN jenis_hak j ON j.kode=b.jenis_hak "
            "GROUP BY j.nama ORDER BY n DESC"):
        print("    %-20s %7d" % (r["nama"], r["n"]))
    for r in kon.execute("SELECT kw, COUNT(*) n FROM bidang GROUP BY kw ORDER BY kw"):
        print("    %-20s %7d" % (r["kw"] or "(kosong)", r["n"]))
    kon.close()


if __name__ == "__main__":
    sys.exit(main())
