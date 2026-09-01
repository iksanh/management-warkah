"""Aplikasi Warkah - katalog, sirkulasi, dan inventarisasi arsip pertanahan.

Jalankan:  python app.py
Lalu buka: http://localhost:8000   (dari komputer lain: http://<ip-server>:8000)
"""
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

import auth
import db
import impor
import impor_residu
import laporan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

PER_HAL = 50
LAMA_PINJAM_HARI = 14

# halaman yang boleh dibuka tanpa masuk
BEBAS = ("/masuk", "/keluar", "/static")


# ------------------------------------------------------------------ bantuan
def pengguna(request: Request):
    """Data pengguna yang sedang masuk, atau None."""
    return getattr(request.state, "pengguna", None)


def petugas_aktif(request: Request) -> str:
    p = pengguna(request)
    return p["nama"] if p else ""


def is_admin(request: Request) -> bool:
    p = pengguna(request)
    return bool(p and p["peran"] == "Admin")


def tolak(request: Request, pesan="Halaman ini hanya untuk Admin."):
    return templates.TemplateResponse(request, "tolak.html",
                                      konteks(request, pesan=pesan), status_code=403)


def hari_ini() -> str:
    return date.today().isoformat()


def sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tulis_log(kon, petugas, aksi, bidang_id=None, rincian=None):
    kon.execute(
        "INSERT INTO log_aktivitas (waktu, petugas, aksi, bidang_id, rincian) "
        "VALUES (?,?,?,?,?)", (sekarang(), petugas or None, aksi, bidang_id, rincian))


def konteks(request: Request, **tambahan):
    dasar = {
        "pengguna": pengguna(request),
        "petugas_aktif": petugas_aktif(request),
        "admin": is_admin(request),
        "pilihan": db.PILIHAN,
        "path": request.url.path,
    }
    dasar.update(tambahan)
    return dasar


def isi(form, nama):
    """Ambil nilai form, string kosong dianggap NULL."""
    v = form.get(nama)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


# SELECT dasar untuk daftar bidang, lengkap dengan status pinjam & periksa
SQL_BIDANG = """
SELECT b.id, b.nomor_hak, b.surat_ukur, b.nib, b.luas, b.produk, b.kw,
       b.pemilik_akhir, b.jenis_hak, j.nama AS nama_hak,
       w.nama_desa, w.nama_kecamatan, w.kode_desa, w.kelompok,
       s.ruang, s.lemari, s.rak, s.box,
       p.bt_ada, p.su_ada, p.status_identifikasi, p.petugas AS petugas_periksa,
       pj.id AS pinjam_id, pj.peminjam, pj.tanggal_pinjam, pj.jatuh_tempo
FROM bidang b
JOIN wilayah w      ON w.id = b.wilayah_id
JOIN jenis_hak j    ON j.kode = b.jenis_hak
LEFT JOIN penyimpanan s ON s.bidang_id = b.id
LEFT JOIN pemeriksaan p ON p.bidang_id = b.id
LEFT JOIN peminjaman pj ON pj.bidang_id = b.id AND pj.tanggal_kembali IS NULL
"""


def bangun_filter(qp):
    """Susun potongan WHERE + parameter dari query string."""
    syarat, par = [], []
    q = (qp.get("q") or "").strip()
    if q:
        pola = "%" + q + "%"
        syarat.append("(b.nomor_hak LIKE ? OR b.nib LIKE ? OR b.surat_ukur LIKE ? "
                      "OR b.pemilik_akhir LIKE ? OR b.pemilik_pertama LIKE ?)")
        par += [pola] * 5
    # desa disaring memakai id wilayah, bukan kode desa: kode desa tidak selalu unik
    # (mis. 18040303 dipakai BUBE dan DUANO, yang harus tetap terpisah)
    for kolom, kunci in (("w.nama_kecamatan", "kecamatan"), ("w.id", "desa"),
                         ("b.jenis_hak", "hak"), ("b.kw", "kw")):
        nilai = (qp.get(kunci) or "").strip()
        if nilai:
            syarat.append("%s = ?" % kolom)
            par.append(nilai)
    sp = (qp.get("pinjam") or "").strip()
    if sp == "dipinjam":
        syarat.append("pj.id IS NOT NULL")
    elif sp == "tersedia":
        syarat.append("pj.id IS NULL")
    pg = (qp.get("petugas") or "").strip()
    if pg:
        syarat.append("p.petugas = ?")
        par.append(pg)
    st = (qp.get("periksa") or "").strip()
    if st == "belum":
        syarat.append("(p.status_identifikasi IS NULL OR p.status_identifikasi = 'Belum Diperiksa')")
    elif st == "sudah":
        syarat.append("(p.status_identifikasi IS NOT NULL AND p.status_identifikasi <> 'Belum Diperiksa')")
    elif st:
        syarat.append("p.status_identifikasi = ?")
        par.append(st)
    where = (" WHERE " + " AND ".join(syarat)) if syarat else ""
    return where, par


# ------------------------------------------------------------------ halaman
async def beranda(request: Request):
    kon = db.sambung()
    ringkas = kon.execute(
        "SELECT (SELECT COUNT(*) FROM bidang) bidang, "
        "       (SELECT COUNT(*) FROM wilayah) desa, "
        "       (SELECT COUNT(DISTINCT nama_kecamatan) FROM wilayah) kecamatan, "
        "       (SELECT COUNT(*) FROM peminjaman WHERE tanggal_kembali IS NULL) dipinjam, "
        "       (SELECT COUNT(*) FROM pemeriksaan WHERE status_identifikasi IS NOT NULL "
        "        AND status_identifikasi <> 'Belum Diperiksa') diperiksa, "
        "       (SELECT COUNT(*) FROM penyimpanan WHERE COALESCE(ruang,lemari,rak,box) IS NOT NULL) berlokasi"
    ).fetchone()
    per_hak = kon.execute(
        "SELECT j.nama, COUNT(b.id) n FROM jenis_hak j LEFT JOIN bidang b ON b.jenis_hak=j.kode "
        "GROUP BY j.kode, j.nama ORDER BY n DESC").fetchall()
    per_kw = kon.execute(
        "SELECT COALESCE(kw,'(kosong)') kw, COUNT(*) n FROM bidang GROUP BY kw ORDER BY kw").fetchall()
    telat = kon.execute(
        "SELECT pj.id, pj.peminjam, pj.jatuh_tempo, b.nomor_hak, w.nama_desa "
        "FROM peminjaman pj JOIN bidang b ON b.id=pj.bidang_id JOIN wilayah w ON w.id=b.wilayah_id "
        "WHERE pj.tanggal_kembali IS NULL AND pj.jatuh_tempo IS NOT NULL AND pj.jatuh_tempo < ? "
        "ORDER BY pj.jatuh_tempo LIMIT 10", (hari_ini(),)).fetchall()
    aktivitas = kon.execute(
        "SELECT * FROM log_aktivitas ORDER BY id DESC LIMIT 12").fetchall()
    top_kec = kon.execute(
        "SELECT w.nama_kecamatan, w.kelompok, COUNT(b.id) n FROM wilayah w "
        "LEFT JOIN bidang b ON b.wilayah_id=w.id GROUP BY w.kelompok, w.nama_kecamatan "
        "ORDER BY n DESC LIMIT 8").fetchall()
    kon.close()
    return templates.TemplateResponse(request, "beranda.html", konteks(
        request, ringkas=ringkas, per_hak=per_hak, per_kw=per_kw, telat=telat,
        aktivitas=aktivitas, top_kec=top_kec))


async def katalog(request: Request):
    qp = request.query_params
    try:
        hal = max(1, int(qp.get("hal", 1)))
    except ValueError:
        hal = 1
    where, par = bangun_filter(qp)

    kon = db.sambung()
    jumlah = kon.execute(
        "SELECT COUNT(*) FROM bidang b JOIN wilayah w ON w.id=b.wilayah_id "
        "LEFT JOIN pemeriksaan p ON p.bidang_id=b.id "
        "LEFT JOIN peminjaman pj ON pj.bidang_id=b.id AND pj.tanggal_kembali IS NULL"
        + where, par).fetchone()[0]
    baris = kon.execute(
        SQL_BIDANG + where + " ORDER BY w.nama_kecamatan, w.nama_desa, b.nomor_hak "
        "LIMIT ? OFFSET ?", par + [PER_HAL, (hal - 1) * PER_HAL]).fetchall()
    kecamatan = kon.execute(
        "SELECT DISTINCT nama_kecamatan FROM wilayah ORDER BY nama_kecamatan").fetchall()
    desa = [dict(r) for r in kon.execute(
        "SELECT id, kode_desa, nama_desa, nama_kecamatan FROM wilayah "
        "ORDER BY nama_kecamatan, nama_desa")]
    daftar_hak = kon.execute("SELECT kode, nama FROM jenis_hak ORDER BY kode").fetchall()
    # nama yang pernah tercatat sebagai pemeriksa, untuk saringan "Petugas periksa"
    petugas_periksa = kon.execute(
        "SELECT DISTINCT petugas FROM pemeriksaan "
        "WHERE NULLIF(TRIM(COALESCE(petugas, '')), '') IS NOT NULL "
        "ORDER BY petugas").fetchall()
    kon.close()

    # daftar desa yang ditampilkan mengikuti kecamatan yang sedang dipilih
    kec_terpilih = (qp.get("kecamatan") or "").strip()
    desa_tampil = [d for d in desa
                   if not kec_terpilih or d["nama_kecamatan"] == kec_terpilih]

    kon2 = db.sambung()
    daftar_petugas = kon2.execute(
        "SELECT nama FROM petugas WHERE aktif = 1 ORDER BY nama").fetchall()
    kon2.close()

    dasar = {k: v for k, v in qp.items() if k != "hal" and v}
    # alamat kembali setelah simpan centang: saringan yang sama, tanpa pesan lama
    sisa = [(k, v) for k, v in qp.multi_items() if k != "tersimpan" and v]
    kembali_ke = "/katalog" + (("?" + "&".join(
        "%s=%s" % (k, quote(v, safe="")) for k, v in sisa)) if sisa else "")

    return templates.TemplateResponse(request, "katalog.html", konteks(
        request, baris=baris, jumlah=jumlah, hal=hal, per_hal=PER_HAL,
        halaman_akhir=max(1, (jumlah + PER_HAL - 1) // PER_HAL),
        kecamatan=kecamatan, desa=desa, desa_tampil=desa_tampil,
        kec_terpilih=kec_terpilih, daftar_hak=daftar_hak, qp=qp, dasar=dasar,
        petugas_periksa=petugas_periksa,
        daftar_petugas=daftar_petugas, hari_ini=hari_ini(), kembali_ke=kembali_ke,
        tersimpan=request.query_params.get("tersimpan")))


async def simpan_centang(request: Request):
    """Simpan centang massal dari halaman katalog.

    Hanya baris yang benar-benar diubah petugas yang dikirim (ditandai di sisi
    peramban), sehingga baris lain tidak ikut tertimpa. Kolom pemeriksaan lain
    — kondisi, warkah, verifikator — tidak disentuh sama sekali.
    """
    form = await request.form()
    siapa = petugas_aktif(request)

    diubah = [i for i in form.getlist("ubah") if i.isdigit()]
    if not diubah:
        return RedirectResponse(form.get("kembali_ke") or "/katalog", status_code=303)

    # petugas hanya boleh mencatat atas namanya sendiri; admin bebas memilih
    petugas_pilihan = isi(form, "petugas") if is_admin(request) else siapa
    tanggal = isi(form, "tanggal") or hari_ini()
    tempat = isi(form, "tempat")
    status_pilihan = isi(form, "status")

    kon = db.sambung()
    n = 0
    for bid in diubah:
        ada_bt = form.get("bt_" + bid) == "1"
        ada_su = form.get("su_" + bid) == "1"
        bt = "Ada" if ada_bt else "Tidak Ada"
        su = "Ada" if ada_su else "Tidak Ada"
        # "(otomatis)" -> Lengkap bila buku tanah dan surat ukur sama-sama ada
        status = status_pilihan or ("Lengkap" if ada_bt and ada_su else "Tidak Lengkap")
        kon.execute(
            "INSERT INTO pemeriksaan (bidang_id, bt_ada, su_ada, status_identifikasi, "
            "petugas, tanggal, tempat, diubah_pada) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (bidang_id) DO UPDATE SET bt_ada=excluded.bt_ada, "
            "su_ada=excluded.su_ada, status_identifikasi=excluded.status_identifikasi, "
            "petugas=excluded.petugas, tanggal=excluded.tanggal, "
            "tempat=COALESCE(excluded.tempat, pemeriksaan.tempat), "
            "diubah_pada=excluded.diubah_pada",
            (int(bid), bt, su, status, petugas_pilihan, tanggal, tempat, sekarang()))
        n += 1

    tulis_log(kon, siapa, "Centang massal katalog", None, "%d bidang" % n)
    kon.commit()
    kon.close()

    tujuan = form.get("kembali_ke") or "/katalog"
    pisah = "&" if "?" in tujuan else "?"
    return RedirectResponse("%s%stersimpan=%d" % (tujuan, pisah, n), status_code=303)


async def detail(request: Request):
    bid = int(request.path_params["bidang_id"])
    kon = db.sambung()
    b = kon.execute(SQL_BIDANG + " WHERE b.id = ?", (bid,)).fetchone()
    if b is None:
        kon.close()
        return Response("Bidang tidak ditemukan", status_code=404)
    pemeriksaan = kon.execute(
        "SELECT * FROM pemeriksaan WHERE bidang_id = ?", (bid,)).fetchone()
    penyimpanan = kon.execute(
        "SELECT * FROM penyimpanan WHERE bidang_id = ?", (bid,)).fetchone()
    riwayat = kon.execute(
        "SELECT * FROM peminjaman WHERE bidang_id = ? ORDER BY id DESC", (bid,)).fetchall()
    detail_bidang = kon.execute(
        "SELECT b.*, w.nama_desa, w.nama_kecamatan, w.kode_desa, w.kelompok, j.nama nama_hak "
        "FROM bidang b JOIN wilayah w ON w.id=b.wilayah_id JOIN jenis_hak j ON j.kode=b.jenis_hak "
        "WHERE b.id = ?", (bid,)).fetchone()
    daftar_petugas = kon.execute(
        "SELECT nama FROM petugas WHERE aktif=1 ORDER BY nama").fetchall()
    baris_residu = kon.execute(
        "SELECT * FROM residu WHERE bidang_id = ?", (bid,)).fetchone()
    kon.close()
    return templates.TemplateResponse(request, "detail.html", konteks(
        request, b=b, d=detail_bidang, pemeriksaan=pemeriksaan, penyimpanan=penyimpanan,
        riwayat=riwayat, daftar_petugas=daftar_petugas, residu=baris_residu,
        hari_ini=hari_ini(),
        jatuh_tempo=(date.today() + timedelta(days=LAMA_PINJAM_HARI)).isoformat()))


async def simpan_bidang(request: Request):
    bid = int(request.path_params["bidang_id"])
    form = await request.form()
    siapa = petugas_aktif(request)
    kon = db.sambung()
    kon.execute(
        "INSERT INTO penyimpanan (bidang_id, ruang, lemari, rak, box, no_urut, catatan, "
        "diubah_oleh, diubah_pada) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (bidang_id) DO UPDATE SET ruang=excluded.ruang, lemari=excluded.lemari, "
        "rak=excluded.rak, box=excluded.box, no_urut=excluded.no_urut, "
        "catatan=excluded.catatan, diubah_oleh=excluded.diubah_oleh, "
        "diubah_pada=excluded.diubah_pada",
        (bid, isi(form, "ruang"), isi(form, "lemari"), isi(form, "rak"), isi(form, "box"),
         isi(form, "no_urut"), isi(form, "catatan_simpan"), siapa or None, sekarang()))
    kolom = ["bt_ada", "bt_kondisi", "bt_sesuai", "su_ada", "su_kondisi", "su_sesuai",
             "warkah_ada", "warkah_no_berkas", "status_identifikasi", "tindak_lanjut",
             "tempat", "tanggal", "petugas", "verifikator", "tanggal_verifikasi", "catatan"]
    nilai = [isi(form, k) for k in kolom]
    # petugas hanya boleh mencatat atas namanya sendiri; admin bebas memilih
    if not is_admin(request):
        nilai[kolom.index("petugas")] = siapa or None
    set_klausa = ", ".join("%s=excluded.%s" % (k, k) for k in kolom)
    kon.execute(
        "INSERT INTO pemeriksaan (bidang_id, %s, diubah_pada) VALUES (?%s,?) "
        "ON CONFLICT (bidang_id) DO UPDATE SET %s, diubah_pada=excluded.diubah_pada"
        % (", ".join(kolom), ",?" * len(kolom), set_klausa),
        [bid] + nilai + [sekarang()])
    tulis_log(kon, siapa, "Simpan lokasi & pemeriksaan", bid,
              isi(form, "status_identifikasi"))
    kon.commit()
    kon.close()
    return RedirectResponse("/bidang/%d?pesan=tersimpan" % bid, status_code=303)


async def pinjam(request: Request):
    bid = int(request.path_params["bidang_id"])
    form = await request.form()
    siapa = petugas_aktif(request)
    kon = db.sambung()
    aktif = kon.execute(
        "SELECT id FROM peminjaman WHERE bidang_id=? AND tanggal_kembali IS NULL",
        (bid,)).fetchone()
    if aktif:
        kon.close()
        return RedirectResponse("/bidang/%d?pesan=masih-dipinjam" % bid, status_code=303)
    kon.execute(
        "INSERT INTO peminjaman (bidang_id, peminjam, unit, keperluan, petugas_pinjam, "
        "tanggal_pinjam, jatuh_tempo, catatan) VALUES (?,?,?,?,?,?,?,?)",
        (bid, isi(form, "peminjam") or "(tanpa nama)", isi(form, "unit"),
         isi(form, "keperluan"), siapa or None,
         isi(form, "tanggal_pinjam") or hari_ini(), isi(form, "jatuh_tempo"),
         isi(form, "catatan_pinjam")))
    tulis_log(kon, siapa, "Peminjaman", bid, isi(form, "peminjam"))
    kon.commit()
    kon.close()
    return RedirectResponse("/bidang/%d?pesan=dipinjam" % bid, status_code=303)


async def kembalikan(request: Request):
    pid = int(request.path_params["pinjam_id"])
    form = await request.form()
    siapa = petugas_aktif(request)
    kon = db.sambung()
    r = kon.execute("SELECT bidang_id FROM peminjaman WHERE id=?", (pid,)).fetchone()
    kon.execute(
        "UPDATE peminjaman SET tanggal_kembali=?, petugas_kembali=?, "
        "catatan=COALESCE(catatan,'') || CASE WHEN ?='' THEN '' ELSE ' | ' || ? END "
        "WHERE id=? AND tanggal_kembali IS NULL",
        (isi(form, "tanggal_kembali") or hari_ini(), siapa or None,
         form.get("catatan_kembali", ""), form.get("catatan_kembali", ""), pid))
    if r:
        tulis_log(kon, siapa, "Pengembalian", r["bidang_id"])
    kon.commit()
    kon.close()
    tujuan = form.get("kembali_ke") or ("/bidang/%d" % r["bidang_id"] if r else "/sirkulasi")
    return RedirectResponse(tujuan, status_code=303)


async def sirkulasi(request: Request):
    qp = request.query_params
    tampil = qp.get("tampil", "aktif")
    kon = db.sambung()
    syarat = "pj.tanggal_kembali IS NULL"
    if tampil == "telat":
        syarat = "pj.tanggal_kembali IS NULL AND pj.jatuh_tempo IS NOT NULL AND pj.jatuh_tempo < '%s'" % hari_ini()
    elif tampil == "selesai":
        syarat = "pj.tanggal_kembali IS NOT NULL"
    elif tampil == "semua":
        syarat = "1=1"
    baris = kon.execute(
        "SELECT pj.*, b.nomor_hak, b.id AS bidang_id, w.nama_desa, w.nama_kecamatan, "
        "s.ruang, s.lemari, s.rak, s.box "
        "FROM peminjaman pj JOIN bidang b ON b.id=pj.bidang_id "
        "JOIN wilayah w ON w.id=b.wilayah_id LEFT JOIN penyimpanan s ON s.bidang_id=b.id "
        "WHERE " + syarat + " ORDER BY pj.tanggal_kembali IS NOT NULL, "
        "COALESCE(pj.jatuh_tempo, pj.tanggal_pinjam), pj.id DESC LIMIT 500").fetchall()
    hitung = kon.execute(
        "SELECT SUM(tanggal_kembali IS NULL) aktif, "
        "SUM(tanggal_kembali IS NULL AND jatuh_tempo IS NOT NULL AND jatuh_tempo < ?) telat, "
        "SUM(tanggal_kembali IS NOT NULL) selesai, COUNT(*) semua "
        "FROM peminjaman", (hari_ini(),)).fetchone()
    kon.close()
    return templates.TemplateResponse(request, "sirkulasi.html", konteks(
        request, baris=baris, hitung=hitung, tampil=tampil, hari_ini=hari_ini()))


# tiga berkas fisik yang didata saat inventarisasi, urut seperti di laporan
JENIS_BERKAS = ("Buku Tanah", "Surat Ukur", "Warkah")

SUDAH_PERIKSA = ("p.status_identifikasi IS NOT NULL "
                 "AND p.status_identifikasi <> 'Belum Diperiksa'")


def saring_periode(qp):
    """Baca tanggal awal/akhir dari query string.

    Kembalikan (dari, sampai, potongan SQL, parameter) untuk menyaring
    pemeriksaan berdasarkan kolom pemeriksaan.tanggal. Bidang yang tanggal
    periksanya kosong otomatis tidak ikut bila periode dipakai.
    """
    def tgl(kunci):
        v = (qp.get(kunci) or "").strip()
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            return None

    dari, sampai = tgl("dari"), tgl("sampai")
    if dari and sampai and dari > sampai:          # terbalik, tukar saja
        dari, sampai = sampai, dari
    syarat, par = [], []
    if dari:
        syarat.append("NULLIF(p.tanggal, '') >= ?")
        par.append(dari)
    if sampai:
        syarat.append("NULLIF(p.tanggal, '') <= ?")
        par.append(sampai)
    return dari, sampai, "".join(" AND " + x for x in syarat), par


def rekap_petugas(kon, periode="", par_periode=()):
    """Rekap kerja per petugas berdasarkan siapa yang benar-benar memeriksa.

    Angka diambil dari kolom pemeriksaan.petugas (bukan dari penugasan desa),
    lengkap dengan rentang tanggal periksa dan daftar desa yang disentuh.
    ``periode`` adalah potongan SQL dari saring_periode(); bila kosong, seluruh
    tanggal ikut terhitung. Data penugasan tetap ditampilkan sebagai pembanding
    target dan sengaja tidak ikut disaring tanggal.
    """
    par = list(par_periode)
    dasar_where = ("WHERE NULLIF(TRIM(COALESCE(p.petugas, '')), '') IS NOT NULL "
                   "AND " + SUDAH_PERIKSA + periode)

    kerja = kon.execute("""
        SELECT p.petugas AS nama,
               COUNT(*) diperiksa,
               COUNT(DISTINCT w.id) desa,
               COUNT(DISTINCT w.nama_kecamatan) kecamatan,
               MIN(NULLIF(p.tanggal, '')) tgl_awal,
               MAX(NULLIF(p.tanggal, '')) tgl_akhir,
               COUNT(DISTINCT NULLIF(p.tanggal, '')) hari,
               SUM(NULLIF(p.tanggal, '') IS NULL) tanpa_tanggal
        FROM pemeriksaan p
        JOIN bidang b  ON b.id = p.bidang_id
        JOIN wilayah w ON w.id = b.wilayah_id
        """ + dasar_where + """
        GROUP BY p.petugas""", par).fetchall()

    rinci = kon.execute("""
        SELECT p.petugas AS nama, w.id wilayah_id, w.kode_desa,
               w.nama_desa, w.nama_kecamatan,
               COUNT(*) bidang,
               SUM(COALESCE(p.bt_ada, '') = 'Ada') bt_ada,
               SUM(COALESCE(p.su_ada, '') = 'Ada') su_ada,
               SUM(COALESCE(p.warkah_ada, '') = 'Ada') wk_ada,
               MIN(NULLIF(p.tanggal, '')) tgl_awal,
               MAX(NULLIF(p.tanggal, '')) tgl_akhir
        FROM pemeriksaan p
        JOIN bidang b  ON b.id = p.bidang_id
        JOIN wilayah w ON w.id = b.wilayah_id
        """ + dasar_where + """
        GROUP BY p.petugas, w.id
        ORDER BY COUNT(*) DESC, w.nama_desa""", par).fetchall()

    status = kon.execute("""
        SELECT p.petugas AS nama, p.status_identifikasi, COUNT(*) bidang
        FROM pemeriksaan p
        """ + dasar_where + """
        GROUP BY p.petugas, p.status_identifikasi
        ORDER BY COUNT(*) DESC""", par).fetchall()

    # kelengkapan fisik berkas: buku tanah, surat ukur, dan warkah
    dokumen = kon.execute("""
        SELECT p.petugas AS nama,
               COALESCE(NULLIF(TRIM(p.bt_ada), ''), '') bt,
               COALESCE(NULLIF(TRIM(p.su_ada), ''), '') su,
               COALESCE(NULLIF(TRIM(p.warkah_ada), ''), '') wk,
               COUNT(*) bidang
        FROM pemeriksaan p
        """ + dasar_where + """
        GROUP BY p.petugas, bt, su, wk""", par).fetchall()

    tugas = kon.execute("""
        SELECT t.petugas AS nama,
               COUNT(DISTINCT t.wilayah_id) desa_tugas,
               COUNT(DISTINCT CASE WHEN t.status = 'Selesai Periksa'
                                   THEN t.wilayah_id END) desa_selesai,
               COUNT(b.id) bidang_tugas,
               SUM(CASE WHEN """ + SUDAH_PERIKSA + """ THEN 1 ELSE 0 END) periksa_tugas
        FROM penugasan t
        LEFT JOIN bidang b      ON b.wilayah_id = t.wilayah_id
        LEFT JOIN pemeriksaan p ON p.bidang_id = b.id
        WHERE NULLIF(TRIM(COALESCE(t.petugas, '')), '') IS NOT NULL
        GROUP BY t.petugas""").fetchall()

    terdaftar = {r["nama"]: r["aktif"] for r in kon.execute(
        "SELECT nama, aktif FROM petugas")}

    baris = {}

    def ambil(nama):
        return baris.setdefault(nama, {
            "nama": nama, "diperiksa": 0, "desa": 0, "kecamatan": 0,
            "tgl_awal": None, "tgl_akhir": None, "hari": 0, "tanpa_tanggal": 0,
            "desa_tugas": 0, "desa_selesai": 0, "bidang_tugas": 0,
            "periksa_tugas": 0, "daftar_desa": [], "status": [],
            "dokumen": {j: {} for j in JENIS_BERKAS}, "bt_su_lengkap": 0,
            "terdaftar": nama in terdaftar, "aktif": terdaftar.get(nama, 0),
        })

    # petugas aktif selalu muncul walau belum memeriksa apa pun
    for nama, aktif in terdaftar.items():
        if aktif:
            ambil(nama)
    for r in kerja:
        ambil(r["nama"]).update(
            {k: r[k] for k in ("diperiksa", "desa", "kecamatan", "tgl_awal",
                               "tgl_akhir", "hari", "tanpa_tanggal")})
    for r in tugas:
        ambil(r["nama"]).update(
            {k: (r[k] or 0) for k in ("desa_tugas", "desa_selesai",
                                      "bidang_tugas", "periksa_tugas")})
    for r in rinci:
        ambil(r["nama"])["daftar_desa"].append(dict(r))
    for r in status:
        ambil(r["nama"])["status"].append(dict(r))
    for r in dokumen:
        b = ambil(r["nama"])
        for jenis, nilai in zip(JENIS_BERKAS, (r["bt"], r["su"], r["wk"])):
            hitung = b["dokumen"][jenis]
            hitung[nilai] = hitung.get(nilai, 0) + r["bidang"]
        if r["bt"] == "Ada" and r["su"] == "Ada":
            b["bt_su_lengkap"] += r["bidang"]

    # nama desa bisa kembar (kode desa tidak selalu unik): tandai agar kode
    # desanya ikut ditampilkan supaya tidak terbaca sebagai desa yang sama
    for r in baris.values():
        jumlah_nama = {}
        for d in r["daftar_desa"]:
            jumlah_nama[d["nama_desa"]] = jumlah_nama.get(d["nama_desa"], 0) + 1
        for d in r["daftar_desa"]:
            d["kembar"] = jumlah_nama[d["nama_desa"]] > 1

    return sorted(baris.values(),
                  key=lambda r: (-r["diperiksa"], r["nama"].lower()))


async def monitoring(request: Request):
    dari, sampai, periode, par = saring_periode(request.query_params)
    kon = db.sambung()
    kec = kon.execute("""
        SELECT w.kelompok, w.nama_kecamatan,
               COUNT(DISTINCT w.id) desa,
               COUNT(b.id) bidang,
               SUM(CASE WHEN """ + SUDAH_PERIKSA + periode + """
                        THEN 1 ELSE 0 END) diperiksa,
               SUM(CASE WHEN s.bidang_id IS NOT NULL THEN 1 ELSE 0 END) berlokasi
        FROM wilayah w
        LEFT JOIN bidang b      ON b.wilayah_id = w.id
        LEFT JOIN pemeriksaan p ON p.bidang_id = b.id
        LEFT JOIN penyimpanan s ON s.bidang_id = b.id
        GROUP BY w.kelompok, w.nama_kecamatan
        ORDER BY w.kelompok, w.nama_kecamatan""", par).fetchall()
    ptg = rekap_petugas(kon, periode, par)
    # bidang yang sudah diperiksa tetapi tidak tercatat siapa pemeriksanya
    tanpa_petugas = kon.execute(
        "SELECT COUNT(*) FROM pemeriksaan p WHERE " + SUDAH_PERIKSA + periode +
        " AND NULLIF(TRIM(COALESCE(p.petugas, '')), '') IS NULL", par).fetchone()[0]
    # tanggal periksa paling awal & akhir di basis data, sebagai batas isian
    batas = kon.execute(
        "SELECT MIN(NULLIF(tanggal, '')) awal, MAX(NULLIF(tanggal, '')) akhir "
        "FROM pemeriksaan").fetchone()
    kon.close()
    return templates.TemplateResponse(request, "monitoring.html", konteks(
        request, kec=kec, ptg=ptg, tanpa_petugas=tanpa_petugas,
        dari=dari or "", sampai=sampai or "",
        batas_awal=batas["awal"] or "", batas_akhir=batas["akhir"] or "",
        teks_periode=laporan.teks_periode(dari, sampai)))


async def laporan_pdf(request: Request):
    """Unduh laporan pemeriksaan dalam bentuk PDF, per petugas."""
    qp = request.query_params
    dari, sampai, periode, par = saring_periode(qp)
    nama = (qp.get("petugas") or "").strip()
    kon = db.sambung()
    daftar = rekap_petugas(kon, periode, par)
    kon.close()
    if nama:
        daftar = [r for r in daftar if r["nama"] == nama]
        if not daftar:
            return tolak(request, "Petugas %s tidak ada dalam rekap." % nama)
    else:
        # laporan gabungan: petugas tanpa pemeriksaan tidak perlu ikut dicetak
        daftar = [r for r in daftar if r["diperiksa"]] or daftar

    isi = laporan.buat_pdf(daftar, dari, sampai,
                           dicetak_oleh=petugas_aktif(request),
                           dicetak_pada=sekarang())
    berkas = "Laporan-Pemeriksaan-%s-%s-sd-%s.pdf" % (
        re.sub(r"[^A-Za-z0-9]+", "-", nama).strip("-") if nama else "Semua-Petugas",
        dari or "awal", sampai or hari_ini())
    return Response(isi, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="%s"' % berkas})


async def monitoring_desa(request: Request):
    qp = request.query_params
    syarat, par = [], []
    if qp.get("kecamatan"):
        syarat.append("w.nama_kecamatan = ?")
        par.append(qp["kecamatan"])
    if qp.get("petugas") == "-":
        syarat.append("t.petugas IS NULL")
    elif qp.get("petugas"):
        syarat.append("t.petugas = ?")
        par.append(qp["petugas"])
    where = (" WHERE " + " AND ".join(syarat)) if syarat else ""
    kon = db.sambung()
    baris = kon.execute("""
        SELECT w.id, w.kelompok, w.kode_kec, w.nama_kecamatan, w.kode_desa, w.nama_desa,
               COUNT(b.id) bidang,
               SUM(CASE WHEN p.status_identifikasi IS NOT NULL
                        AND p.status_identifikasi <> 'Belum Diperiksa' THEN 1 ELSE 0 END) diperiksa,
               t.petugas, t.tanggal_mulai, t.target_selesai, t.tanggal_selesai,
               t.status, t.kendala, t.catatan
        FROM wilayah w
        LEFT JOIN bidang b      ON b.wilayah_id = w.id
        LEFT JOIN pemeriksaan p ON p.bidang_id = b.id
        LEFT JOIN penugasan t   ON t.wilayah_id = w.id""" + where + """
        GROUP BY w.id ORDER BY w.kelompok, w.nama_kecamatan, w.nama_desa""", par).fetchall()
    kecamatan = kon.execute(
        "SELECT DISTINCT nama_kecamatan FROM wilayah ORDER BY nama_kecamatan").fetchall()
    daftar_petugas = kon.execute(
        "SELECT nama FROM petugas WHERE aktif=1 ORDER BY nama").fetchall()
    kon.close()
    return templates.TemplateResponse(request, "monitoring_desa.html", konteks(
        request, baris=baris, kecamatan=kecamatan, daftar_petugas=daftar_petugas, qp=qp))


async def simpan_penugasan(request: Request):
    if not is_admin(request):
        return tolak(request, "Penugasan desa hanya bisa diatur oleh Admin.")
    wid = int(request.path_params["wilayah_id"])
    form = await request.form()
    kon = db.sambung()
    kon.execute(
        "INSERT INTO penugasan (wilayah_id, petugas, tanggal_mulai, target_selesai, "
        "tanggal_selesai, status, kendala, catatan) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT (wilayah_id) DO UPDATE SET petugas=excluded.petugas, "
        "tanggal_mulai=excluded.tanggal_mulai, target_selesai=excluded.target_selesai, "
        "tanggal_selesai=excluded.tanggal_selesai, status=excluded.status, "
        "kendala=excluded.kendala, catatan=excluded.catatan",
        (wid, isi(form, "petugas"), isi(form, "tanggal_mulai"), isi(form, "target_selesai"),
         isi(form, "tanggal_selesai"), isi(form, "status"), isi(form, "kendala"),
         isi(form, "catatan")))
    tulis_log(kon, petugas_aktif(request), "Penugasan desa", None, isi(form, "petugas"))
    kon.commit()
    kon.close()
    return RedirectResponse(form.get("kembali_ke") or "/monitoring/desa", status_code=303)


# ------------------------------------------------------------------ rekap desa
# urutan kolom KW pada tabel rekap; kolom terakhir menampung KW yang kosong
KOLOM_KW = ["KW1", "KW2", "KW3", "KW4", "KW5", "KW6"]

SQL_REKAP = """
SELECT w.id, w.kelompok, w.kode_kec, w.nama_kecamatan, w.kode_desa, w.nama_desa,
       COUNT(b.id) bidang,
       SUM(CASE WHEN b.kw = 'KW1' THEN 1 ELSE 0 END) kw1,
       SUM(CASE WHEN b.kw = 'KW2' THEN 1 ELSE 0 END) kw2,
       SUM(CASE WHEN b.kw = 'KW3' THEN 1 ELSE 0 END) kw3,
       SUM(CASE WHEN b.kw = 'KW4' THEN 1 ELSE 0 END) kw4,
       SUM(CASE WHEN b.kw = 'KW5' THEN 1 ELSE 0 END) kw5,
       SUM(CASE WHEN b.kw = 'KW6' THEN 1 ELSE 0 END) kw6,
       SUM(CASE WHEN b.id IS NOT NULL AND NULLIF(TRIM(COALESCE(b.kw, '')), '') IS NULL
                THEN 1 ELSE 0 END) kw0,
       k.jumlah AS kkp, k.catatan AS catatan_kkp
FROM wilayah w
LEFT JOIN bidang b    ON b.wilayah_id = w.id
LEFT JOIN rekap_kkp k ON k.wilayah_id = w.id
"""

KUNCI_ANGKA = ("bidang", "kw1", "kw2", "kw3", "kw4", "kw5", "kw6", "kw0")

# jumlah bidang satu desa, dipakai saringan agar cocok dengan kolom Bidang
HITUNG_BIDANG = "(SELECT COUNT(*) FROM bidang b2 WHERE b2.wilayah_id = w.id)"


def saring_rekap(qp):
    """Potongan WHERE untuk halaman rekap dari query string."""
    syarat, par = [], []
    for kolom, kunci in (("w.kelompok", "kelompok"), ("w.kode_kec", "kode_kec"),
                         ("w.nama_kecamatan", "kecamatan")):
        nilai = (qp.get(kunci) or "").strip()
        if nilai:
            syarat.append("%s = ?" % kolom)
            par.append(nilai)
    beda = (qp.get("beda") or "").strip()
    if beda == "beda":
        syarat.append("k.jumlah IS NOT NULL AND k.jumlah <> " + HITUNG_BIDANG)
    elif beda == "cocok":
        syarat.append("k.jumlah IS NOT NULL AND k.jumlah = " + HITUNG_BIDANG)
    elif beda == "belum":
        syarat.append("k.jumlah IS NULL")
    elif beda == "kosong":
        syarat.append(HITUNG_BIDANG + " = 0")
    return (" WHERE " + " AND ".join(syarat)) if syarat else "", par


def kumpulkan_rekap(kon, qp):
    """Baris rekap desa dikelompokkan per kecamatan, lengkap dengan subtotalnya."""
    where, par = saring_rekap(qp)
    baris = kon.execute(
        SQL_REKAP + where + " GROUP BY w.id "
        "ORDER BY w.kelompok, w.kode_kec, w.nama_kecamatan, w.kode_desa, w.nama_desa",
        par).fetchall()

    grup = []
    total = {k: 0 for k in KUNCI_ANGKA}
    total.update({"kkp": 0, "desa": 0, "terisi": 0, "beda": 0, "belum": 0, "kosong": 0})
    for r in baris:
        d = dict(r)
        d["selisih"] = (d["bidang"] - d["kkp"]) if d["kkp"] is not None else None
        kunci = (d["kelompok"], d["kode_kec"], d["nama_kecamatan"])
        if not grup or grup[-1]["kunci"] != kunci:
            awal = {k: 0 for k in KUNCI_ANGKA}
            awal.update({"kunci": kunci, "kelompok": d["kelompok"],
                         "kode_kec": d["kode_kec"], "nama": d["nama_kecamatan"],
                         "desa": [], "jumlah_desa": 0, "kkp": 0, "ada_kkp": False})
            grup.append(awal)
        g = grup[-1]
        g["desa"].append(d)
        g["jumlah_desa"] += 1
        total["desa"] += 1
        for k in KUNCI_ANGKA:
            g[k] += d[k]
            total[k] += d[k]
        if not d["bidang"]:
            total["kosong"] += 1
        if d["kkp"] is None:
            total["belum"] += 1
        else:
            g["kkp"] += d["kkp"]
            g["ada_kkp"] = True
            total["kkp"] += d["kkp"]
            total["terisi"] += 1
            if d["selisih"]:
                total["beda"] += 1
    for g in grup:
        g["selisih"] = (g["bidang"] - g["kkp"]) if g["ada_kkp"] else None
    total["selisih"] = (total["bidang"] - total["kkp"]) if total["terisi"] else None
    total["kecamatan"] = len(grup)
    return grup, total


async def rekap(request: Request):
    """Rekap jumlah bidang per desa per kecamatan, untuk dicocokkan dengan KKP."""
    kon = db.sambung()
    grup, total = kumpulkan_rekap(kon, request.query_params)
    daftar_kec = kon.execute(
        "SELECT kelompok, kode_kec, nama_kecamatan, COUNT(*) desa FROM wilayah "
        "GROUP BY kelompok, kode_kec, nama_kecamatan "
        "ORDER BY kelompok, kode_kec, nama_kecamatan").fetchall()
    # angka seluruh basis data, tetap ditampilkan walau tabel sedang disaring
    semua = kon.execute(
        "SELECT (SELECT COUNT(*) FROM bidang) bidang, "
        "(SELECT COUNT(*) FROM wilayah) desa, "
        "(SELECT COUNT(*) FROM rekap_kkp WHERE jumlah IS NOT NULL) terisi, "
        "(SELECT COALESCE(SUM(jumlah), 0) FROM rekap_kkp) kkp").fetchone()
    kon.close()
    # query string dirakit di sini, bukan di templat: penyaring urlencode Jinja
    # tidak mengenali QueryParams dan akan membacanya sebagai daftar kunci saja
    kueri = str(request.url.query)
    return templates.TemplateResponse(request, "rekap.html", konteks(
        request, grup=grup, total=total, daftar_kec=daftar_kec, semua=semua,
        kolom_kw=KOLOM_KW, qp=request.query_params, kueri=kueri,
        tautan=request.url.path + (("?" + kueri) if kueri else "")))


def sel_csv(v):
    """Satu sel CSV; pemisah titik koma supaya langsung rapi di Excel."""
    t = "" if v is None else str(v)
    if any(c in t for c in ';"\r\n'):
        return '"' + t.replace('"', '""') + '"'
    return t


JUDUL_CSV = ["Kelompok", "Kode_Kecamatan", "Kecamatan", "Kode_Desa", "Desa",
             "Bidang_Terinput", "KW1", "KW2", "KW3", "KW4", "KW5", "KW6",
             "Tanpa_KW", "Jumlah_KKP", "Selisih", "Catatan"]


async def rekap_csv(request: Request):
    """Unduh rekap sebagai CSV agar bisa disandingkan dengan tarikan KKP di Excel."""
    kon = db.sambung()
    grup, total = kumpulkan_rekap(kon, request.query_params)
    kon.close()

    angka = ("bidang", "kw1", "kw2", "kw3", "kw4", "kw5", "kw6", "kw0")
    larik = [";".join(JUDUL_CSV)]
    for g in grup:
        for d in g["desa"]:
            larik.append(";".join(sel_csv(v) for v in
                                  [d["kelompok"], d["kode_kec"], d["nama_kecamatan"],
                                   d["kode_desa"], d["nama_desa"]]
                                  + [d[k] for k in angka]
                                  + [d["kkp"], d["selisih"], d["catatan_kkp"]]))
        larik.append(";".join(sel_csv(v) for v in
                              [g["kelompok"], g["kode_kec"], "JUMLAH " + g["nama"], "", ""]
                              + [g[k] for k in angka]
                              + [g["kkp"] if g["ada_kkp"] else None, g["selisih"], ""]))
    larik.append(";".join(sel_csv(v) for v in
                          ["", "", "JUMLAH SELURUHNYA", "", ""]
                          + [total[k] for k in angka]
                          + [total["kkp"] if total["terisi"] else None,
                             total["selisih"], ""]))

    nama = "rekap_bidang_%s.csv" % hari_ini()
    return Response("﻿" + "\r\n".join(larik) + "\r\n",
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % nama})


async def simpan_kkp(request: Request):
    """Simpan angka pembanding KKP yang diketik di halaman rekap."""
    if not is_admin(request):
        return tolak(request, "Angka pembanding KKP hanya bisa diubah oleh Admin.")
    form = await request.form()
    kon = db.sambung()
    diisi, dihapus = 0, 0
    for kunci in form:
        if not kunci.startswith("kkp_"):
            continue
        try:
            wid = int(kunci[4:])
        except ValueError:
            continue
        nilai = isi(form, kunci)
        if nilai is None:
            dihapus += kon.execute("DELETE FROM rekap_kkp WHERE wilayah_id = ?",
                                   (wid,)).rowcount
            continue
        try:
            jumlah = int(float(nilai.replace(".", "").replace(",", ".")))
        except ValueError:
            continue
        kon.execute(
            "INSERT INTO rekap_kkp (wilayah_id, jumlah, catatan, diubah_oleh, "
            "diubah_pada) VALUES (?,?,?,?,?) ON CONFLICT (wilayah_id) DO UPDATE SET "
            "jumlah=excluded.jumlah, catatan=excluded.catatan, "
            "diubah_oleh=excluded.diubah_oleh, diubah_pada=excluded.diubah_pada",
            (wid, jumlah, isi(form, "catatan_%d" % wid), petugas_aktif(request),
             sekarang()))
        diisi += 1
    if diisi or dihapus:
        tulis_log(kon, petugas_aktif(request), "Isi angka pembanding KKP", None,
                  "%d desa diisi, %d dikosongkan" % (diisi, dihapus))
    kon.commit()
    kon.close()
    return RedirectResponse(form.get("kembali_ke") or "/rekap", status_code=303)


# ------------------------------------------------------------------ impor data
MAKS_BERKAS = 400


def ringkas_impor():
    """Angka besar isi basis data untuk kepala halaman impor."""
    kon = db.sambung()
    r = kon.execute(
        "SELECT (SELECT COUNT(*) FROM bidang) bidang, "
        "(SELECT COUNT(*) FROM wilayah) desa, "
        "(SELECT COUNT(DISTINCT wilayah_id) FROM bidang) desa_terisi").fetchone()
    kon.close()
    return r


async def halaman_impor(request: Request):
    """Borang unggah berkas data bidang (format folder per_desa)."""
    if not is_admin(request):
        return tolak(request, "Impor data hanya bisa dilakukan oleh Admin.")
    return templates.TemplateResponse(request, "impor.html", konteks(
        request, ringkas=ringkas_impor(), mode_pilihan=impor.MODE, hasil=None,
        terpilih={"mode": "perbarui", "buat_desa": False, "arsip": True}))


async def jalankan_impor(request: Request):
    if not is_admin(request):
        return tolak(request, "Impor data hanya bisa dilakukan oleh Admin.")

    try:
        form = await request.form(max_files=MAKS_BERKAS + 10, max_fields=50)
    except MultiPartException:
        return templates.TemplateResponse(request, "impor.html", konteks(
            request, ringkas=ringkas_impor(), mode_pilihan=impor.MODE, hasil=None,
            galat="Unggahan tidak terbaca. Kirim paling banyak %d berkas sekali "
                  "jalan." % MAKS_BERKAS,
            terpilih={"mode": "perbarui", "buat_desa": False, "arsip": True}),
            status_code=400)
    mode = form.get("mode") if form.get("mode") in impor.MODE else "perbarui"
    buat_desa = form.get("buat_desa") == "1"
    arsip = form.get("arsip") == "1"
    simpan = form.get("aksi") == "simpan"

    berkas, galat = [], None
    for item in form.getlist("berkas"):
        if not isinstance(item, UploadFile) or not item.filename:
            continue
        berkas.append((item.filename, await item.read()))
        await item.close()
    if len(berkas) > MAKS_BERKAS:
        galat = "Maksimal %d berkas sekali unggah, yang dipilih %d." % (
            MAKS_BERKAS, len(berkas))
    elif not berkas:
        galat = "Belum ada berkas yang dipilih."

    hasil = None
    if not galat:
        try:
            hasil = impor.jalankan(berkas, mode=mode, buat_desa=buat_desa,
                                   simpan=simpan, arsip=arsip)
        except Exception as e:                                  # noqa: BLE001
            galat = "Impor dihentikan, tidak ada perubahan yang disimpan: %s" % e

    if hasil and simpan:
        t = hasil["total"]
        kon = db.sambung()
        tulis_log(kon, petugas_aktif(request), "Impor data bidang", None,
                  "%d berkas, %d desa, mode %s: %d baru, %d diperbarui, %d dibuang"
                  % (t["berkas"], t["desa"], mode, t["baru"], t["diperbarui"],
                     t["dibuang"]))
        kon.commit()
        kon.close()

    return templates.TemplateResponse(request, "impor.html", konteks(
        request, ringkas=ringkas_impor(), mode_pilihan=impor.MODE, hasil=hasil,
        galat=galat, terpilih={"mode": mode, "buat_desa": buat_desa, "arsip": arsip}))


# ------------------------------------------------------------------- residu
# Residu PTSL: sertipikat yang sudah terbit tetapi belum diserahkan ke pemohon.
# Satu baris residu menempel satu-lawan-satu ke satu bidang lewat bidang_id.

SQL_RESIDU = """
SELECT r.*, w.nama_desa, w.nama_kecamatan, w.kode_desa,
       b.surat_ukur, b.nib, b.pemilik_akhir, b.luas AS luas_bidang,
       s.ruang, s.lemari, s.rak, s.box
FROM residu r
LEFT JOIN wilayah w     ON w.id = r.wilayah_id
LEFT JOIN bidang b      ON b.id = r.bidang_id
LEFT JOIN penyimpanan s ON s.bidang_id = r.bidang_id
"""

# kolom kerja: hanya ini yang boleh diubah petugas, sisanya milik hasil impor
KOLOM_KERJA = ["status", "tindak_lanjut", "petugas", "tanggal_serah", "penerima",
               "catatan"]


def filter_residu(qp, kecuali=()):
    """Susun potongan WHERE + parameter untuk daftar residu.

    ``kecuali`` berisi nama saringan yang dilewati; dipakai untuk menghitung
    jumlah per tahun tanpa ikut terpotong oleh tahun yang sedang dipilih.
    """
    syarat, par = [], []
    q = (qp.get("q") or "").strip() if "q" not in kecuali else ""
    if q:
        pola = "%" + q + "%"
        # tahun ikut dicari supaya mengetik "2021" di kotak kata kunci langsung
        # menyaring tahun anggarannya, bukan cuma nomor berkas yang memuat 2021
        syarat.append("(r.tahun LIKE ? OR r.nomor_hak LIKE ? OR r.nomor_hak_asli LIKE ? "
                      "OR r.nama_pemegang LIKE ? OR r.nomor_berkas LIKE ? "
                      "OR r.no_seri_blanko LIKE ?)")
        par += [pola] * 6
    for kolom, kunci in (("r.tahun", "tahun"), ("w.nama_kecamatan", "kecamatan"),
                         ("r.wilayah_id", "desa"), ("r.status", "status"),
                         ("r.petugas", "petugas")):
        nilai = (qp.get(kunci) or "").strip() if kunci not in kecuali else ""
        if nilai:
            syarat.append("%s = ?" % kolom)
            par.append(nilai)

    t = (qp.get("tipologi") or "").strip() if "tipologi" not in kecuali else ""
    if t:
        # tipologi disimpan sebagai daftar kode dipisah koma, mis. "T1.1,T2.2";
        # koma pembungkus dipakai agar T1.1 tidak ikut cocok dengan T1.10
        syarat.append("(',' || r.tipologi || ',') LIKE ?")
        par.append("%," + t + ",%")
    elif (qp.get("tipologi_kosong") or "") == "1":
        syarat.append("NULLIF(TRIM(COALESCE(r.tipologi, '')), '') IS NULL")

    serah = (qp.get("serah") or "").strip() if "serah" not in kecuali else ""
    if serah == "belum":
        syarat.append("r.sudah_diserahkan = 0")
    elif serah == "sudah":
        syarat.append("r.sudah_diserahkan = 1")

    bl = (qp.get("blanko") or "").strip() if "blanko" not in kecuali else ""
    if bl == "belum":
        syarat.append("r.blanko_ada IS NULL")
    elif bl:
        syarat.append("r.blanko_ada = ?")
        par.append(bl)

    cocok = (qp.get("cocok") or "").strip() if "cocok" not in kecuali else ""
    if cocok == "cocok":
        syarat.append("r.bidang_id IS NOT NULL")
    elif cocok == "belum":
        syarat.append("r.bidang_id IS NULL")

    where = (" WHERE " + " AND ".join(syarat)) if syarat else ""
    return where, par


def angka_residu(kon, where="", par=()):
    """Angka besar untuk kepala halaman residu."""
    return kon.execute(
        "SELECT COUNT(*) jumlah, "
        "SUM(CASE WHEN r.sudah_diserahkan = 1 THEN 1 ELSE 0 END) diserahkan, "
        "SUM(CASE WHEN r.bidang_id IS NOT NULL THEN 1 ELSE 0 END) cocok, "
        "SUM(CASE WHEN r.status IS NOT NULL AND r.status <> 'Belum Ditindaklanjuti' "
        "         THEN 1 ELSE 0 END) ditindaklanjuti, "
        "SUM(CASE WHEN r.blanko_ada = 'Ada' THEN 1 ELSE 0 END) blanko_ada, "
        "SUM(CASE WHEN r.blanko_ada IS NULL THEN 1 ELSE 0 END) blanko_belum "
        "FROM residu r LEFT JOIN wilayah w ON w.id = r.wilayah_id" + where,
        list(par)).fetchone()


def rekap_tahun(kon, qp):
    """Jumlah residu per tahun anggaran, mengikuti saringan selain tahun.

    Tahun dihitung tanpa memperhatikan tahun yang sedang dipilih supaya tab
    tahun tetap menampilkan angka seluruh tahun.
    """
    where, par = filter_residu(qp, kecuali=("tahun",))
    return kon.execute(
        "SELECT COALESCE(r.tahun, '(tanpa tahun)') tahun, COUNT(*) jumlah, "
        "SUM(CASE WHEN r.sudah_diserahkan = 1 THEN 1 ELSE 0 END) diserahkan, "
        "SUM(CASE WHEN r.bidang_id IS NOT NULL THEN 1 ELSE 0 END) cocok "
        "FROM residu r LEFT JOIN wilayah w ON w.id = r.wilayah_id" + where
        + " GROUP BY r.tahun ORDER BY r.tahun", par).fetchall()


async def residu(request: Request):
    qp = request.query_params
    try:
        hal = max(1, int(qp.get("hal", 1)))
    except ValueError:
        hal = 1
    where, par = filter_residu(qp)

    kon = db.sambung()
    ringkas = angka_residu(kon, where, par)
    jumlah = ringkas["jumlah"]
    baris = kon.execute(
        SQL_RESIDU + where + " ORDER BY r.tahun, w.nama_kecamatan, w.nama_desa, "
        "r.nomor_hak LIMIT ? OFFSET ?", par + [PER_HAL, (hal - 1) * PER_HAL]).fetchall()

    per_tahun = rekap_tahun(kon, qp)
    # daftar isian tahun sengaja tidak ikut disaring, supaya tahun yang sedang
    # dipilih tetap ada di dalam kotaknya walau saringan lain mengosongkannya
    semua_tahun = kon.execute(
        "SELECT DISTINCT tahun FROM residu WHERE tahun IS NOT NULL "
        "ORDER BY tahun").fetchall()
    kecamatan = kon.execute(
        "SELECT DISTINCT w.nama_kecamatan FROM residu r JOIN wilayah w "
        "ON w.id = r.wilayah_id ORDER BY w.nama_kecamatan").fetchall()
    desa = [dict(r) for r in kon.execute(
        "SELECT DISTINCT w.id, w.nama_desa, w.nama_kecamatan FROM residu r "
        "JOIN wilayah w ON w.id = r.wilayah_id "
        "ORDER BY w.nama_kecamatan, w.nama_desa")]
    daftar_tipologi = kon.execute(
        "SELECT kode, kelompok, nama FROM tipologi ORDER BY urut").fetchall()
    # berapa baris memakai tiap kode tipologi, mengikuti saringan yang sedang aktif
    hitung_tipologi = {}
    for t in daftar_tipologi:
        hitung_tipologi[t["kode"]] = kon.execute(
            "SELECT COUNT(*) FROM residu r LEFT JOIN wilayah w ON w.id = r.wilayah_id"
            + (where + " AND " if where else " WHERE ")
            + "(',' || r.tipologi || ',') LIKE ?", par + ["%," + t["kode"] + ",%"]
        ).fetchone()[0]
    daftar_petugas = kon.execute(
        "SELECT nama FROM petugas WHERE aktif = 1 ORDER BY nama").fetchall()
    kon.close()

    kec_terpilih = (qp.get("kecamatan") or "").strip()
    desa_tampil = [d for d in desa
                   if not kec_terpilih or d["nama_kecamatan"] == kec_terpilih]

    dasar = {k: v for k, v in qp.items() if k != "hal" and v}
    sisa = [(k, v) for k, v in qp.multi_items() if k != "tersimpan" and v]
    kembali_ke = "/residu" + (("?" + "&".join(
        "%s=%s" % (k, quote(v, safe="")) for k, v in sisa)) if sisa else "")

    return templates.TemplateResponse(request, "residu.html", konteks(
        request, baris=baris, jumlah=jumlah, ringkas=ringkas, hal=hal,
        per_hal=PER_HAL, halaman_akhir=max(1, (jumlah + PER_HAL - 1) // PER_HAL),
        per_tahun=per_tahun, semua_tahun=semua_tahun,
        kecamatan=kecamatan, desa=desa, desa_tampil=desa_tampil,
        kec_terpilih=kec_terpilih, daftar_tipologi=daftar_tipologi,
        hitung_tipologi=hitung_tipologi, daftar_petugas=daftar_petugas,
        qp=qp, dasar=dasar, kembali_ke=kembali_ke, hari_ini=hari_ini(),
        tersimpan=qp.get("tersimpan")))


async def simpan_residu(request: Request):
    """Simpan kolom kerja satu baris residu; kolom hasil impor tidak disentuh."""
    rid = int(request.path_params["residu_id"])
    form = await request.form()
    siapa = petugas_aktif(request)

    nilai = [isi(form, k) for k in KOLOM_KERJA]
    # petugas hanya boleh mencatat atas namanya sendiri; admin bebas memilih
    if not is_admin(request):
        nilai[KOLOM_KERJA.index("petugas")] = siapa or None
    serah = 1 if form.get("sudah_diserahkan") == "1" else 0

    kon = db.sambung()
    ada = kon.execute("SELECT nomor_hak FROM residu WHERE id = ?", (rid,)).fetchone()
    if ada is None:
        kon.close()
        return Response("Data residu tidak ditemukan", status_code=404)
    kon.execute(
        "UPDATE residu SET %s, sudah_diserahkan = ?, diubah_oleh = ?, "
        "diubah_pada = ? WHERE id = ?"
        % ", ".join("%s = ?" % k for k in KOLOM_KERJA),
        nilai + [serah, siapa or None, sekarang(), rid])
    tulis_log(kon, siapa, "Simpan residu", None,
              "%s: %s" % (ada["nomor_hak"] or "-", isi(form, "status") or "-"))
    kon.commit()
    kon.close()

    tujuan = form.get("kembali_ke") or "/residu"
    pisah = "&" if "?" in tujuan else "?"
    return RedirectResponse("%s%stersimpan=1" % (tujuan, pisah), status_code=303)


async def centang_blanko(request: Request):
    """Simpan centang blanko massal dari halaman residu.

    Sama seperti centang BT/SU di katalog: hanya baris yang benar-benar diubah
    petugas yang dikirim, sehingga baris lain tidak ikut tertimpa. Ini semata
    mencatat kondisi yang ada - blanko fisiknya ketemu atau tidak - dan tidak
    dicocokkan dengan nomor seri blanko di berkas.
    """
    form = await request.form()
    siapa = petugas_aktif(request)

    diubah = [i for i in form.getlist("ubah") if i.isdigit()]
    if not diubah:
        return RedirectResponse(form.get("kembali_ke") or "/residu", status_code=303)

    # petugas hanya boleh mencatat atas namanya sendiri; admin bebas memilih
    petugas_pilihan = isi(form, "petugas") if is_admin(request) else siapa
    tanggal = isi(form, "tanggal") or hari_ini()
    tempat = isi(form, "tempat")

    kon = db.sambung()
    n = 0
    for rid in diubah:
        ada = "Ada" if form.get("blanko_" + rid) == "1" else "Tidak Ada"
        cur = kon.execute(
            "UPDATE residu SET blanko_ada = ?, blanko_petugas = ?, "
            "blanko_tanggal = ?, blanko_tempat = COALESCE(?, blanko_tempat), "
            "blanko_diubah = ? WHERE id = ?",
            (ada, petugas_pilihan, tanggal, tempat, sekarang(), int(rid)))
        n += cur.rowcount

    tulis_log(kon, siapa, "Centang blanko residu", None, "%d baris" % n)
    kon.commit()
    kon.close()

    tujuan = form.get("kembali_ke") or "/residu"
    pisah = "&" if "?" in tujuan else "?"
    return RedirectResponse("%s%stercentang=%d" % (tujuan, pisah, n), status_code=303)


# --------------------------------------------- rekap pengecekan blanko residu
# Bagian residu yang dipakai berulang oleh rekap: baris residu beserta nama
# wilayahnya, nama petugas pencentang, dan tanggal centangnya. blanko_ada hanya
# terisi lewat tombol centang di halaman Residu, jadi "tidak kosong" berarti
# blankonya memang sudah pernah dicek petugas.
DARI_BLANKO = " FROM residu r LEFT JOIN wilayah w ON w.id = r.wilayah_id"
SUDAH_DICEK = "NULLIF(TRIM(COALESCE(r.blanko_ada, '')), '') IS NOT NULL"
PETUGAS_BLANKO = "COALESCE(NULLIF(TRIM(r.blanko_petugas), ''), '(tanpa petugas)')"
TANGGAL_BLANKO = "COALESCE(NULLIF(r.blanko_tanggal, ''), '(tanpa tanggal)')"
KEC_BLANKO = ("COALESCE(NULLIF(TRIM(w.nama_kecamatan), ''), "
              "NULLIF(TRIM(r.kecamatan_teks), ''), '(tanpa kecamatan)')")
DESA_BLANKO = ("COALESCE(NULLIF(TRIM(w.nama_desa), ''), "
               "NULLIF(TRIM(r.desa_teks), ''), '(tanpa desa)')")


def saring_blanko(qp):
    """Susun saringan halaman rekap pengecekan blanko.

    Dipisah dua supaya progres per desa punya penyebut yang benar: ``umum``
    hanya menyaring tahun dan wilayah sehingga berlaku untuk seluruh baris
    residu di desa itu, sedangkan ``cek`` menambahkan syarat blankonya sudah
    dicek berikut batas tanggal, petugas, dan hasil pengecekannya.
    """
    def tgl(kunci):
        v = (qp.get(kunci) or "").strip()
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            return None

    dari, sampai = tgl("dari"), tgl("sampai")
    if dari and sampai and dari > sampai:          # terbalik, tukar saja
        dari, sampai = sampai, dari

    umum, par_umum = [], []
    for kolom, kunci in (("r.tahun", "tahun"), ("w.nama_kecamatan", "kecamatan"),
                         ("r.wilayah_id", "desa")):
        nilai = (qp.get(kunci) or "").strip()
        if nilai:
            umum.append("%s = ?" % kolom)
            par_umum.append(nilai)

    cek, par_cek = [SUDAH_DICEK], []
    if dari:
        cek.append("NULLIF(r.blanko_tanggal, '') >= ?")
        par_cek.append(dari)
    if sampai:
        cek.append("NULLIF(r.blanko_tanggal, '') <= ?")
        par_cek.append(sampai)
    nama = (qp.get("petugas") or "").strip()
    if nama:
        cek.append(PETUGAS_BLANKO + " = ?")
        par_cek.append(nama)
    hasil = (qp.get("hasil") or "").strip()
    if hasil:
        cek.append("r.blanko_ada = ?")
        par_cek.append(hasil)

    return {
        "dari": dari or "", "sampai": sampai or "", "petugas": nama, "hasil": hasil,
        "where_umum": (" WHERE " + " AND ".join(umum)) if umum else "",
        "par_umum": par_umum,
        "where_cek": " WHERE " + " AND ".join(umum + cek),
        "par_cek": par_umum + par_cek,
    }


def rincian_blanko(kon, s):
    """Satu baris per tanggal + petugas + desa; inti rekap hariannya.

    Dipakai bersama oleh halaman rekap dan unduhan CSV-nya supaya angka di
    layar dan di berkas selalu sama.
    """
    return kon.execute(
        "SELECT " + TANGGAL_BLANKO + " tanggal, " + PETUGAS_BLANKO + " nama, "
        + KEC_BLANKO + " kecamatan, " + DESA_BLANKO + " desa, r.wilayah_id, "
        "COUNT(*) dicek, "
        "SUM(r.blanko_ada = 'Ada') ada, "
        "SUM(r.blanko_ada <> 'Ada') tidak_ada, "
        "GROUP_CONCAT(DISTINCT NULLIF(TRIM(r.blanko_tempat), '')) tempat"
        + DARI_BLANKO + s["where_cek"] +
        " GROUP BY tanggal, nama, kecamatan, desa "
        "ORDER BY tanggal DESC, nama, kecamatan, desa", s["par_cek"]).fetchall()


async def rekap_blanko(request: Request):
    """Rekap harian pengecekan blanko, per petugas dan per desa/kecamatan."""
    qp = request.query_params
    s = saring_blanko(qp)
    where, par = s["where_cek"], s["par_cek"]

    kon = db.sambung()
    ringkas = kon.execute(
        "SELECT COUNT(*) dicek, "
        "SUM(r.blanko_ada = 'Ada') ada, "
        "SUM(r.blanko_ada <> 'Ada') tidak_ada, "
        "COUNT(DISTINCT NULLIF(r.blanko_tanggal, '')) hari, "
        "COUNT(DISTINCT " + PETUGAS_BLANKO + ") petugas, "
        "COUNT(DISTINCT " + DESA_BLANKO + ") desa, "
        "COUNT(DISTINCT " + KEC_BLANKO + ") kecamatan, "
        "SUM(NULLIF(r.blanko_tanggal, '') IS NULL) tanpa_tanggal"
        + DARI_BLANKO + where, par).fetchone()

    # seluruh baris residu di tahun/wilayah yang sedang dipilih, sebagai
    # pembanding: berapa yang sama sekali belum pernah dicek blankonya
    lingkup = kon.execute(
        "SELECT COUNT(*) jumlah, SUM(NOT (" + SUDAH_DICEK + ")) belum"
        + DARI_BLANKO + s["where_umum"], s["par_umum"]).fetchone()

    per_petugas = kon.execute(
        "SELECT " + PETUGAS_BLANKO + " nama, COUNT(*) dicek, "
        "SUM(r.blanko_ada = 'Ada') ada, "
        "SUM(r.blanko_ada <> 'Ada') tidak_ada, "
        "COUNT(DISTINCT NULLIF(r.blanko_tanggal, '')) hari, "
        "COUNT(DISTINCT " + DESA_BLANKO + ") desa, "
        "COUNT(DISTINCT " + KEC_BLANKO + ") kecamatan, "
        "MIN(NULLIF(r.blanko_tanggal, '')) tgl_awal, "
        "MAX(NULLIF(r.blanko_tanggal, '')) tgl_akhir"
        + DARI_BLANKO + where + " GROUP BY nama ORDER BY dicek DESC, nama",
        par).fetchall()

    harian = kon.execute(
        "SELECT " + TANGGAL_BLANKO + " tanggal, " + PETUGAS_BLANKO + " nama, "
        "COUNT(*) dicek, "
        "SUM(r.blanko_ada = 'Ada') ada, "
        "SUM(r.blanko_ada <> 'Ada') tidak_ada, "
        "COUNT(DISTINCT " + DESA_BLANKO + ") desa, "
        "GROUP_CONCAT(DISTINCT " + DESA_BLANKO + ") daftar_desa, "
        "GROUP_CONCAT(DISTINCT " + KEC_BLANKO + ") daftar_kecamatan"
        + DARI_BLANKO + where + " GROUP BY tanggal, nama "
        "ORDER BY tanggal DESC, dicek DESC, nama", par).fetchall()

    # jumlah per hari saja, dipakai sebagai baris pembuka tiap tanggal
    per_hari = {x["tanggal"]: x for x in kon.execute(
        "SELECT " + TANGGAL_BLANKO + " tanggal, COUNT(*) dicek, "
        "SUM(r.blanko_ada = 'Ada') ada, "
        "SUM(r.blanko_ada <> 'Ada') tidak_ada, "
        "COUNT(DISTINCT " + PETUGAS_BLANKO + ") petugas, "
        "COUNT(DISTINCT " + DESA_BLANKO + ") desa"
        + DARI_BLANKO + where + " GROUP BY tanggal", par)}

    per_desa = [dict(x) for x in kon.execute(
        "SELECT " + KEC_BLANKO + " kecamatan, " + DESA_BLANKO + " desa, "
        "COUNT(*) dicek, "
        "SUM(r.blanko_ada = 'Ada') ada, "
        "SUM(r.blanko_ada <> 'Ada') tidak_ada, "
        "COUNT(DISTINCT NULLIF(r.blanko_tanggal, '')) hari, "
        "MIN(NULLIF(r.blanko_tanggal, '')) tgl_awal, "
        "MAX(NULLIF(r.blanko_tanggal, '')) tgl_akhir, "
        "GROUP_CONCAT(DISTINCT " + PETUGAS_BLANKO + ") daftar_petugas"
        + DARI_BLANKO + where + " GROUP BY kecamatan, desa "
        "ORDER BY kecamatan, desa", par)]

    # penyebut progres per desa sengaja tidak ikut disaring tanggal/petugas:
    # yang ditanya "berapa isi desa ini seluruhnya", bukan "berapa hari itu"
    seluruh = {}
    for t in kon.execute(
            "SELECT " + KEC_BLANKO + " kecamatan, " + DESA_BLANKO + " desa, "
            "COUNT(*) jumlah, SUM(NOT (" + SUDAH_DICEK + ")) belum"
            + DARI_BLANKO + s["where_umum"] + " GROUP BY kecamatan, desa",
            s["par_umum"]):
        seluruh[(t["kecamatan"], t["desa"])] = t
    for d in per_desa:
        t = seluruh.get((d["kecamatan"], d["desa"]))
        d["total"] = t["jumlah"] if t else d["dicek"]
        d["belum"] = (t["belum"] or 0) if t else 0

    rincian = rincian_blanko(kon, s)

    # isian saringan; petugas diambil dari yang benar-benar pernah mencentang
    batas = kon.execute(
        "SELECT MIN(NULLIF(blanko_tanggal, '')) awal, "
        "MAX(NULLIF(blanko_tanggal, '')) akhir FROM residu").fetchone()
    daftar_petugas = [x[0] for x in kon.execute(
        "SELECT DISTINCT " + PETUGAS_BLANKO + " FROM residu r WHERE "
        + SUDAH_DICEK + " ORDER BY 1")]
    semua_tahun = kon.execute(
        "SELECT DISTINCT tahun FROM residu WHERE tahun IS NOT NULL "
        "ORDER BY tahun").fetchall()
    kecamatan = kon.execute(
        "SELECT DISTINCT w.nama_kecamatan FROM residu r JOIN wilayah w "
        "ON w.id = r.wilayah_id ORDER BY w.nama_kecamatan").fetchall()
    desa = [dict(x) for x in kon.execute(
        "SELECT DISTINCT w.id, w.nama_desa, w.nama_kecamatan FROM residu r "
        "JOIN wilayah w ON w.id = r.wilayah_id "
        "ORDER BY w.nama_kecamatan, w.nama_desa")]
    kon.close()

    kec_terpilih = (qp.get("kecamatan") or "").strip()
    desa_tampil = [d for d in desa
                   if not kec_terpilih or d["nama_kecamatan"] == kec_terpilih]
    kueri = "&".join("%s=%s" % (k, quote(v, safe=""))
                     for k, v in qp.multi_items() if v)

    return templates.TemplateResponse(request, "residu_rekap.html", konteks(
        request, ringkas=ringkas, lingkup=lingkup, per_petugas=per_petugas,
        harian=harian, per_hari=per_hari, per_desa=per_desa, rincian=rincian,
        semua_tahun=semua_tahun, kecamatan=kecamatan, desa=desa,
        desa_tampil=desa_tampil, kec_terpilih=kec_terpilih,
        daftar_petugas=daftar_petugas, qp=qp, kueri=kueri,
        dari=s["dari"], sampai=s["sampai"], hasil=s["hasil"],
        batas_awal=batas["awal"] or "", batas_akhir=batas["akhir"] or "",
        teks_periode=laporan.teks_periode(s["dari"], s["sampai"])))


JUDUL_REKAP_BLANKO_CSV = ["Tanggal", "Petugas", "Kecamatan", "Desa",
                          "Blanko_Dicek", "Blanko_Ada", "Blanko_Tidak_Ada",
                          "Tempat"]


async def rekap_blanko_csv(request: Request):
    """Unduh rekap harian pengecekan blanko per petugas dan desa."""
    s = saring_blanko(request.query_params)
    kon = db.sambung()
    baris = rincian_blanko(kon, s)
    kon.close()

    keluar = [";".join(JUDUL_REKAP_BLANKO_CSV)]
    for r in baris:
        keluar.append(";".join(sel_csv(v) for v in (
            r["tanggal"], r["nama"], r["kecamatan"], r["desa"],
            r["dicek"], r["ada"] or 0, r["tidak_ada"] or 0, r["tempat"])))

    isi_csv = "﻿" + "\r\n".join(keluar) + "\r\n"
    berkas = "rekap_blanko_%s_sd_%s.csv" % (s["dari"] or "awal",
                                            s["sampai"] or hari_ini())
    return Response(isi_csv.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="%s"' % berkas})


JUDUL_RESIDU_CSV = ["Tahun", "Nomor_Berkas", "Nomor_Hak", "Nomor_Hak_Asli",
                    "Kecamatan", "Desa", "Nama_Pemegang", "No_Seri_Blanko",
                    "Luas", "Sudah_Diserahkan", "Tipologi", "Keterangan",
                    "Status", "Tindak_Lanjut", "Petugas", "Tanggal_Serah",
                    "Penerima", "Catatan", "Blanko_Ada", "Blanko_Petugas",
                    "Blanko_Tanggal", "Cocok_Dengan_Bidang", "Surat_Ukur", "NIB"]


async def residu_csv(request: Request):
    qp = request.query_params
    where, par = filter_residu(qp)
    kon = db.sambung()
    baris = kon.execute(
        SQL_RESIDU + where + " ORDER BY r.tahun, w.nama_kecamatan, w.nama_desa, "
        "r.nomor_hak", par).fetchall()
    kon.close()

    keluar = [";".join(JUDUL_RESIDU_CSV)]
    for r in baris:
        keluar.append(";".join(sel_csv(v) for v in (
            r["tahun"], r["nomor_berkas"], r["nomor_hak"], r["nomor_hak_asli"],
            r["nama_kecamatan"] or r["kecamatan_teks"],
            r["nama_desa"] or r["desa_teks"], r["nama_pemegang"],
            r["no_seri_blanko"], r["luas"],
            "Sudah" if r["sudah_diserahkan"] else "Belum",
            r["tipologi"], r["keterangan"], r["status"], r["tindak_lanjut"],
            r["petugas"], r["tanggal_serah"], r["penerima"], r["catatan"],
            r["blanko_ada"] or "Belum Dicek", r["blanko_petugas"],
            r["blanko_tanggal"],
            "Ya" if r["bidang_id"] else "Belum", r["surat_ukur"], r["nib"])))

    isi_csv = "﻿" + "\r\n".join(keluar) + "\r\n"
    # nama berkas ikut menyebut tahun supaya unduhan tiap tahun tidak tertukar
    th = re.sub(r"[^0-9]", "", (qp.get("tahun") or ""))[:4]
    return Response(isi_csv.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="residu_ptsl%s.csv"'
                             % (("_" + th) if th else "")})


async def simpan_tipologi(request: Request):
    """Isi/ubah keterangan kode tipologi; keterangannya tidak ada di berkas Excel."""
    if not is_admin(request):
        return tolak(request, "Keterangan tipologi hanya bisa diubah oleh Admin.")
    form = await request.form()
    kon = db.sambung()
    for r in kon.execute("SELECT kode FROM tipologi").fetchall():
        kunci = "nama_" + r["kode"]
        if kunci in form:
            kon.execute("UPDATE tipologi SET nama = ? WHERE kode = ?",
                        (isi(form, kunci), r["kode"]))
    tulis_log(kon, petugas_aktif(request), "Ubah keterangan tipologi residu")
    kon.commit()
    kon.close()
    return RedirectResponse(form.get("kembali_ke") or "/residu", status_code=303)


async def cocokkan_residu(request: Request):
    """Cocokkan ulang baris residu yang bidang_id-nya masih kosong."""
    if not is_admin(request):
        return tolak(request, "Pencocokan ulang hanya bisa dilakukan oleh Admin.")
    form = await request.form()
    kon = db.sambung()
    n = impor_residu.cocokkan_ulang(kon)
    tulis_log(kon, petugas_aktif(request), "Cocokkan ulang residu", None,
              "%d baris tercocokkan" % n)
    kon.commit()
    kon.close()
    tujuan = form.get("kembali_ke") or "/residu"
    pisah = "&" if "?" in tujuan else "?"
    return RedirectResponse("%s%stercocok=%d" % (tujuan, pisah, n), status_code=303)


def ringkas_residu():
    """Angka besar isi tabel residu untuk kepala halaman impor."""
    kon = db.sambung()
    r = kon.execute(
        "SELECT COUNT(*) baris, "
        "SUM(CASE WHEN bidang_id IS NOT NULL THEN 1 ELSE 0 END) cocok, "
        "COUNT(DISTINCT tahun) tahun FROM residu").fetchone()
    kon.close()
    return r


TERPILIH_RESIDU = {"mode": "perbarui", "arsip": True}


async def halaman_impor_residu(request: Request):
    """Borang unggah berkas RESIDU PTSL."""
    if not is_admin(request):
        return tolak(request, "Impor residu hanya bisa dilakukan oleh Admin.")
    return templates.TemplateResponse(request, "residu_impor.html", konteks(
        request, ringkas=ringkas_residu(), mode_pilihan=impor_residu.MODE,
        hasil=None, terpilih=dict(TERPILIH_RESIDU)))


async def jalankan_impor_residu(request: Request):
    if not is_admin(request):
        return tolak(request, "Impor residu hanya bisa dilakukan oleh Admin.")

    try:
        form = await request.form(max_files=MAKS_BERKAS + 10, max_fields=50)
    except MultiPartException:
        return templates.TemplateResponse(request, "residu_impor.html", konteks(
            request, ringkas=ringkas_residu(), mode_pilihan=impor_residu.MODE,
            hasil=None, galat="Unggahan tidak terbaca. Kirim paling banyak %d "
                              "berkas sekali jalan." % MAKS_BERKAS,
            terpilih=dict(TERPILIH_RESIDU)), status_code=400)

    mode = form.get("mode") if form.get("mode") in impor_residu.MODE else "perbarui"
    arsip = form.get("arsip") == "1"
    simpan = form.get("aksi") == "simpan"

    berkas, galat = [], None
    for item in form.getlist("berkas"):
        if not isinstance(item, UploadFile) or not item.filename:
            continue
        berkas.append((item.filename, await item.read()))
        await item.close()
    if len(berkas) > MAKS_BERKAS:
        galat = "Maksimal %d berkas sekali unggah, yang dipilih %d." % (
            MAKS_BERKAS, len(berkas))
    elif not berkas:
        galat = "Belum ada berkas yang dipilih."

    hasil = None
    if not galat:
        try:
            hasil = impor_residu.jalankan(berkas, mode=mode, simpan=simpan,
                                          arsip=arsip)
        except Exception as e:                                  # noqa: BLE001
            galat = "Impor dihentikan, tidak ada perubahan yang disimpan: %s" % e

    if hasil and simpan:
        t = hasil["total"]
        kon = db.sambung()
        tulis_log(kon, petugas_aktif(request), "Impor residu PTSL", None,
                  "%d berkas, %d lembar, mode %s: %d baru, %d diperbarui, "
                  "%d cocok" % (t["berkas"], t["lembar"], mode, t["baru"],
                                t["diperbarui"], t["cocok"]))
        kon.commit()
        kon.close()

    return templates.TemplateResponse(request, "residu_impor.html", konteks(
        request, ringkas=ringkas_residu(), mode_pilihan=impor_residu.MODE,
        hasil=hasil, galat=galat, terpilih={"mode": mode, "arsip": arsip}))


# --------------------------------------------------------------- akun & masuk
async def masuk(request: Request):
    tujuan = request.query_params.get("next") or "/"
    if request.method == "GET":
        if request.session.get("uid"):
            return RedirectResponse(tujuan, status_code=303)
        return templates.TemplateResponse(request, "masuk.html", konteks(
            request, tujuan=tujuan, galat=None))

    form = await request.form()
    nama_akun = (form.get("username") or "").strip().lower()
    sandi = form.get("sandi") or ""
    tujuan = form.get("next") or "/"

    kon = db.sambung()
    r = kon.execute(
        "SELECT id, nama, peran, aktif, sandi FROM petugas WHERE username = ?",
        (nama_akun,)).fetchone()
    sah = r is not None and r["aktif"] == 1 and auth.cek_sandi(sandi, r["sandi"])
    if sah:
        kon.execute("UPDATE petugas SET terakhir_masuk = ? WHERE id = ?",
                    (sekarang(), r["id"]))
        tulis_log(kon, r["nama"], "Masuk aplikasi")
        kon.commit()
    kon.close()

    if not sah:
        return templates.TemplateResponse(request, "masuk.html", konteks(
            request, tujuan=tujuan, galat="Nama pengguna atau sandi salah, "
                                          "atau akun sudah dinonaktifkan."),
            status_code=401)

    request.session.clear()
    request.session["uid"] = r["id"]
    return RedirectResponse(tujuan, status_code=303)


async def keluar(request: Request):
    request.session.clear()
    return RedirectResponse("/masuk", status_code=303)


async def ganti_sandi(request: Request):
    p = pengguna(request)
    if request.method == "GET":
        return templates.TemplateResponse(request, "ganti_sandi.html",
                                          konteks(request, galat=None, sukses=False))
    form = await request.form()
    lama = form.get("sandi_lama") or ""
    baru = form.get("sandi_baru") or ""
    ulang = form.get("sandi_ulang") or ""

    kon = db.sambung()
    r = kon.execute("SELECT sandi FROM petugas WHERE id = ?", (p["id"],)).fetchone()
    galat = None
    if not auth.cek_sandi(lama, r["sandi"]):
        galat = "Sandi lama salah."
    elif len(baru) < 8:
        galat = "Sandi baru minimal 8 karakter."
    elif baru != ulang:
        galat = "Ulangan sandi baru tidak sama."
    if galat:
        kon.close()
        return templates.TemplateResponse(request, "ganti_sandi.html",
                                          konteks(request, galat=galat, sukses=False))
    kon.execute("UPDATE petugas SET sandi = ?, sandi_diubah_pada = ? WHERE id = ?",
                (auth.buat_sandi(baru), sekarang(), p["id"]))
    tulis_log(kon, p["nama"], "Ganti sandi")
    kon.commit()
    kon.close()
    return templates.TemplateResponse(request, "ganti_sandi.html",
                                      konteks(request, galat=None, sukses=True))


async def daftar_pengguna(request: Request):
    if not is_admin(request):
        return tolak(request)
    kon = db.sambung()
    baris = kon.execute(
        "SELECT p.id, p.nama, p.username, p.peran, p.aktif, p.terakhir_masuk, "
        "p.sandi_diubah_pada, (p.sandi IS NOT NULL) AS ada_sandi, "
        "(SELECT COUNT(*) FROM penugasan t WHERE t.petugas = p.nama) AS desa "
        "FROM petugas p ORDER BY p.peran, p.nama").fetchall()
    kon.close()
    return templates.TemplateResponse(request, "pengguna.html", konteks(
        request, baris=baris, peran=auth.PERAN,
        sandi_baru=request.query_params.get("sandi"),
        untuk=request.query_params.get("untuk")))


async def simpan_pengguna(request: Request):
    if not is_admin(request):
        return tolak(request)
    form = await request.form()
    aksi = form.get("aksi")
    p = pengguna(request)
    kon = db.sambung()
    tujuan = "/pengguna"

    if aksi == "tambah":
        nama = isi(form, "nama")
        akun = (isi(form, "username") or "").lower()
        peran = form.get("peran") if form.get("peran") in auth.PERAN else "Petugas"
        if nama and akun:
            bentrok = kon.execute(
                "SELECT id FROM petugas WHERE nama = ? OR username = ?",
                (nama, akun)).fetchone()
            sandi = auth.sandi_acak()
            if bentrok:
                kon.execute("UPDATE petugas SET username=?, sandi=?, peran=?, aktif=1, "
                            "sandi_diubah_pada=? WHERE id=?",
                            (akun, auth.buat_sandi(sandi), peran, sekarang(), bentrok["id"]))
            else:
                kon.execute("INSERT INTO petugas (nama, username, sandi, peran, aktif, "
                            "dibuat_pada, sandi_diubah_pada) VALUES (?,?,?,?,1,?,?)",
                            (nama, akun, auth.buat_sandi(sandi), peran,
                             sekarang(), sekarang()))
            tulis_log(kon, p["nama"], "Tambah/ubah akun", None, akun)
            tujuan = "/pengguna?sandi=%s&untuk=%s" % (sandi, akun)

    elif aksi == "sandi":
        akun = isi(form, "username")
        sandi = auth.sandi_acak()
        kon.execute("UPDATE petugas SET sandi=?, sandi_diubah_pada=? WHERE username=?",
                    (auth.buat_sandi(sandi), sekarang(), akun))
        tulis_log(kon, p["nama"], "Atur ulang sandi", None, akun)
        tujuan = "/pengguna?sandi=%s&untuk=%s" % (sandi, akun)

    elif aksi in ("aktif", "nonaktif"):
        akun = isi(form, "username")
        # jangan sampai admin terakhir dimatikan atau menonaktifkan dirinya sendiri
        sisa = kon.execute(
            "SELECT COUNT(*) FROM petugas WHERE peran='Admin' AND aktif=1 AND username <> ?",
            (akun,)).fetchone()[0]
        if aksi == "nonaktif" and sisa == 0:
            kon.close()
            return tolak(request, "Tidak bisa menonaktifkan admin aktif yang terakhir.")
        kon.execute("UPDATE petugas SET aktif=? WHERE username=?",
                    (1 if aksi == "aktif" else 0, akun))
        tulis_log(kon, p["nama"], "Ubah status akun", None, "%s -> %s" % (akun, aksi))

    elif aksi == "peran":
        akun = isi(form, "username")
        peran = form.get("peran") if form.get("peran") in auth.PERAN else "Petugas"
        sisa = kon.execute(
            "SELECT COUNT(*) FROM petugas WHERE peran='Admin' AND aktif=1 AND username <> ?",
            (akun,)).fetchone()[0]
        if peran != "Admin" and sisa == 0:
            kon.close()
            return tolak(request, "Tidak bisa menurunkan peran admin aktif yang terakhir.")
        kon.execute("UPDATE petugas SET peran=? WHERE username=?", (peran, akun))
        tulis_log(kon, p["nama"], "Ubah peran akun", None, "%s -> %s" % (akun, peran))

    kon.commit()
    kon.close()
    return RedirectResponse(tujuan, status_code=303)


class WajibMasuk(BaseHTTPMiddleware):
    """Semua halaman butuh sesi yang sah, kecuali daftar BEBAS."""

    async def dispatch(self, request, call_next):
        jalur = request.url.path
        if jalur.startswith(BEBAS):
            return await call_next(request)

        uid = request.session.get("uid")
        if uid:
            kon = db.sambung()
            r = kon.execute(
                "SELECT id, nama, username, peran, aktif FROM petugas WHERE id = ?",
                (uid,)).fetchone()
            kon.close()
            if r and r["aktif"] == 1:
                request.state.pengguna = dict(r)
                return await call_next(request)
            request.session.clear()

        tujuan = jalur + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/masuk?next=" + quote(tujuan, safe=""), status_code=303)


rute = [
    Route("/", beranda),
    Route("/katalog", katalog),
    Route("/katalog/centang", simpan_centang, methods=["POST"]),
    Route("/bidang/{bidang_id:int}", detail),
    Route("/bidang/{bidang_id:int}/simpan", simpan_bidang, methods=["POST"]),
    Route("/bidang/{bidang_id:int}/pinjam", pinjam, methods=["POST"]),
    Route("/pinjam/{pinjam_id:int}/kembali", kembalikan, methods=["POST"]),
    Route("/sirkulasi", sirkulasi),
    Route("/monitoring", monitoring),
    Route("/monitoring/laporan.pdf", laporan_pdf),
    Route("/monitoring/desa", monitoring_desa),
    Route("/penugasan/{wilayah_id:int}", simpan_penugasan, methods=["POST"]),
    Route("/masuk", masuk, methods=["GET", "POST"]),
    Route("/keluar", keluar),
    Route("/ganti-sandi", ganti_sandi, methods=["GET", "POST"]),
    Route("/pengguna", daftar_pengguna),
    Route("/pengguna/simpan", simpan_pengguna, methods=["POST"]),
    Route("/rekap", rekap),
    Route("/rekap.csv", rekap_csv),
    Route("/rekap/kkp", simpan_kkp, methods=["POST"]),
    Route("/impor", halaman_impor),
    Route("/impor/jalankan", jalankan_impor, methods=["POST"]),
    Route("/residu", residu),
    Route("/residu.csv", residu_csv),
    Route("/residu/rekap", rekap_blanko),
    Route("/residu/rekap.csv", rekap_blanko_csv),
    Route("/residu/centang", centang_blanko, methods=["POST"]),
    Route("/residu/{residu_id:int}/simpan", simpan_residu, methods=["POST"]),
    Route("/residu/tipologi", simpan_tipologi, methods=["POST"]),
    Route("/residu/cocokkan", cocokkan_residu, methods=["POST"]),
    Route("/residu/impor", halaman_impor_residu),
    Route("/residu/impor/jalankan", jalankan_impor_residu, methods=["POST"]),
    Mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"),
]

lapisan = [
    Middleware(SessionMiddleware,
               secret_key=auth.kunci_rahasia(os.path.join(BASE_DIR, "rahasia.txt")),
               session_cookie="warkah_sesi", max_age=60 * 60 * 12, same_site="lax"),
    Middleware(WajibMasuk),
]

app = Starlette(routes=rute, middleware=lapisan)


def format_angka(n):
    try:
        return "{:,}".format(int(n)).replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def persen(a, b):
    try:
        return (float(a) / float(b) * 100) if b else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def versi_gaya():
    """Penanda versi berkas gaya, dipakai agar peramban tidak memakai
    tampilan lama dari cache setelah style.css diperbarui."""
    try:
        return str(int(os.path.getmtime(os.path.join(BASE_DIR, "static", "style.css"))))
    except OSError:
        return "1"


templates.env.filters["angka"] = format_angka
templates.env.filters["tanggal"] = laporan.tanggal_id
templates.env.globals["persen"] = persen
templates.env.globals["hari_ini"] = hari_ini
templates.env.globals["versi_gaya"] = versi_gaya


if __name__ == "__main__":
    db.siapkan().close()
    print("Aplikasi Warkah berjalan.")
    print("  Di komputer ini      : http://localhost:8000")
    print("  Dari komputer lain   : http://<alamat-ip-server>:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
