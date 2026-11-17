"""Pembuat berkas PDF laporan pemeriksaan warkah per petugas.

Dipakai oleh rute /monitoring/laporan.pdf di app.py. Data yang masuk sudah
berupa hasil rekap (lihat app.rekap_petugas), modul ini hanya menatanya.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (CondPageBreak, KeepTogether, LongTable, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

KANTOR = "Kantor Pertanahan Kabupaten Bone Bolango"
BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]

ABU = colors.HexColor("#e5e7eb")
ABU_TUA = colors.HexColor("#374151")
BIRU = colors.HexColor("#1d4ed8")

_gaya = getSampleStyleSheet()
JUDUL = ParagraphStyle("judul", parent=_gaya["Title"], fontSize=14, leading=18,
                       spaceAfter=2, alignment=TA_CENTER)
SUBJUDUL = ParagraphStyle("subjudul", parent=_gaya["Normal"], fontSize=10,
                          leading=13, alignment=TA_CENTER, textColor=ABU_TUA)
BAGIAN = ParagraphStyle("bagian", parent=_gaya["Normal"], fontName="Helvetica-Bold",
                        fontSize=10, leading=13, spaceBefore=10, spaceAfter=4)
ISI = ParagraphStyle("isi", parent=_gaya["Normal"], fontSize=9, leading=12)
KECIL = ParagraphStyle("kecil", parent=_gaya["Normal"], fontSize=8, leading=11,
                       textColor=ABU_TUA, spaceBefore=4)
SEL = ParagraphStyle("sel", parent=_gaya["Normal"], fontSize=8.5, leading=10.5)
KEPALA = ParagraphStyle("kepala", parent=_gaya["Normal"], fontName="Helvetica-Bold",
                        fontSize=8, leading=9.5, textColor=ABU_TUA)
KEPALA_KANAN = ParagraphStyle("kepala_kanan", parent=KEPALA, alignment=TA_RIGHT)

# urutan nilai kolom "ada" (lihat db.PILIHAN); nilai kosong = belum dicek
NILAI_ADA = ["Ada", "Tidak Ada", "Tidak Ditemukan", "Dipinjam", "Rusak/Hilang"]
BELUM_DICEK = "Belum dicek"
# kolom yang selalu tampil walau nol, supaya bentuk tabel sama antar petugas
NILAI_TETAP = ("Ada", "Tidak Ada", BELUM_DICEK)


def angka(n):
    """1234 -> '1.234' (pemisah ribuan gaya Indonesia)."""
    try:
        return "{:,}".format(int(n)).replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def tanggal_id(iso, kosong="-"):
    """'2026-08-27' -> '27 Agustus 2026'."""
    if not iso:
        return kosong
    bagian = str(iso)[:10].split("-")
    if len(bagian) != 3:
        return str(iso)
    try:
        return "%d %s %s" % (int(bagian[2]), BULAN[int(bagian[1])], bagian[0])
    except (ValueError, IndexError):
        return str(iso)


def tanggal_pendek(iso, kosong="-"):
    """'2026-08-27' -> '27-08-2026' (dipakai di tabel yang kolomnya sempit)."""
    if not iso:
        return kosong
    bagian = str(iso)[:10].split("-")
    return "-".join(reversed(bagian)) if len(bagian) == 3 else str(iso)


def teks_periode(dari, sampai):
    """Kalimat periode untuk kepala laporan."""
    if dari and sampai:
        return "%s s/d %s" % (tanggal_id(dari), tanggal_id(sampai))
    if dari:
        return "Sejak %s" % tanggal_id(dari)
    if sampai:
        return "Sampai %s" % tanggal_id(sampai)
    return "Seluruh periode"


def _kaki(kanvas, dok):
    """Garis dan keterangan di kaki setiap halaman."""
    kanvas.saveState()
    kanvas.setStrokeColor(ABU)
    kanvas.line(15 * mm, 14 * mm, A4[0] - 15 * mm, 14 * mm)
    kanvas.setFont("Helvetica", 7.5)
    kanvas.setFillColor(ABU_TUA)
    kanvas.drawString(15 * mm, 10 * mm, dok.keterangan_kaki)
    kanvas.drawRightString(A4[0] - 15 * mm, 10 * mm,
                           "Halaman %d" % kanvas.getPageNumber())
    kanvas.restoreState()


def _tabel(data, lebar, rata_kanan=()):
    """Tabel baku: kepala abu-abu, garis tipis, baris selang-seling."""
    t = Table(data, colWidths=lebar, repeatRows=1, hAlign="LEFT")
    gaya = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), ABU),
        ("TEXTCOLOR", (0, 0), (-1, 0), ABU_TUA),
        ("GRID", (0, 0), (-1, -1), 0.4, ABU),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f9fafb")]),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8.5),
        ("BACKGROUND", (0, -1), (-1, -1), ABU),
    ]
    for k in rata_kanan:
        gaya.append(("ALIGN", (k, 0), (k, -1), "RIGHT"))
    t.setStyle(TableStyle(gaya))
    return t


def _ringkasan(r, dari, sampai):
    """Kotak keterangan di bawah judul: siapa, periode, dan angka pokoknya."""
    rerata = round(r["diperiksa"] / r["hari"]) if r["hari"] else 0
    baris = [
        ["Petugas pemeriksa", ": %s" % r["nama"],
         "Bidang diperiksa", ": %s bidang" % angka(r["diperiksa"])],
        ["Periode laporan", ": %s" % teks_periode(dari, sampai),
         "Jumlah desa", ": %s desa" % angka(r["desa"])],
        ["Tanggal periksa", ": %s" % (
            "%s s/d %s" % (tanggal_id(r["tgl_awal"]), tanggal_id(r["tgl_akhir"]))
            if r["tgl_awal"] else "-"),
         "Jumlah kecamatan", ": %s kecamatan" % angka(r["kecamatan"])],
        ["Hari kerja tercatat", ": %s hari" % angka(r["hari"]),
         "Rata-rata per hari", ": %s bidang" % angka(rerata)],
    ]
    t = Table(baris, colWidths=[35 * mm, 62 * mm, 31 * mm, 52 * mm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (1, 0), (1, 0), "Helvetica-Bold", 9),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, ABU),
    ]))
    return t


def _tanda_tangan(nama, dicetak_pada):
    """Blok tanda tangan di kanan bawah laporan."""
    t = Table([
        ["", "Bone Bolango, %s" % tanggal_id(dicetak_pada[:10])],
        ["", "Petugas pemeriksa,"],
        ["", ""],
        ["", ""],
        ["", nama],
    ], colWidths=[110 * mm, 60 * mm], rowHeights=[12, 12, 14, 14, 14])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (1, -1), (1, -1), "Helvetica-Bold", 9),
        ("LINEABOVE", (1, -1), (1, -1), 0.6, ABU_TUA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _kelengkapan(r):
    """Tabel matriks: buku tanah / surat ukur / warkah lawan kondisi berkasnya.

    Kolom mengikuti nilai yang benar-benar ada di data (Ada, Tidak Ada, dan
    seterusnya), ditambah tiga kolom baku supaya bentuknya seragam.
    """
    dokumen = r["dokumen"]
    dipakai = set()
    for hitung in dokumen.values():
        dipakai |= {k for k, n in hitung.items() if n}
    kolom = [n for n in NILAI_ADA if n in dipakai or n in NILAI_TETAP]
    if "" in dipakai or BELUM_DICEK in NILAI_TETAP:
        kolom.append(BELUM_DICEK)

    kepala = [Paragraph("Jenis berkas", KEPALA)]
    kepala += [Paragraph(k, KEPALA_KANAN) for k in kolom]
    kepala.append(Paragraph("% Ada", KEPALA_KANAN))

    baris = [kepala]
    for jenis in ("Buku Tanah", "Surat Ukur", "Warkah"):
        hitung = dokumen.get(jenis, {})
        isi_baris = [jenis]
        for k in kolom:
            isi_baris.append(angka(hitung.get("" if k == BELUM_DICEK else k, 0)))
        ada = hitung.get("Ada", 0)
        isi_baris.append(("%.1f%%" % (ada / r["diperiksa"] * 100
                                      if r["diperiksa"] else 0)).replace(".", ","))
        baris.append(isi_baris)

    # kolom nilai dibagi rata dari sisa lebar, tetapi tidak dibiarkan melebar
    # berlebihan saat nilainya sedikit
    lebar_nilai = min(30.0, (180 - 45 - 22) / len(kolom))
    lebar = [45 * mm] + [lebar_nilai * mm for _ in kolom] + [22 * mm]
    t = _tabel(baris, lebar, rata_kanan=tuple(range(1, len(kolom) + 2)))
    # baris terakhir di sini bukan baris jumlah, jadi tebalnya dibatalkan
    t.setStyle(TableStyle([
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f9fafb")]),
    ]))
    return t


def _catatan_kelengkapan(r):
    """Kalimat penutup bagian kelengkapan: yang lengkap dan yang perlu ditindak."""
    n = r["diperiksa"]
    if not n:
        return None
    dokumen = r["dokumen"]
    bt_hilang = n - dokumen.get("Buku Tanah", {}).get("Ada", 0)
    su_hilang = n - dokumen.get("Surat Ukur", {}).get("Ada", 0)
    belum_wk = dokumen.get("Warkah", {}).get("", 0)
    porsi = ("%.1f%%" % (r["bt_su_lengkap"] / n * 100)).replace(".", ",")
    teks = ("Buku tanah dan surat ukur lengkap pada <b>%s dari %s bidang</b> (%s). "
            "Buku tanah belum ditemukan: %s bidang; surat ukur belum ditemukan: "
            "%s bidang." % (angka(r["bt_su_lengkap"]), angka(n), porsi,
                            angka(bt_hilang), angka(su_hilang)))
    if belum_wk:
        teks += (" Keberadaan warkah belum didata pada %s bidang." % angka(belum_wk))
    return Paragraph(teks, KECIL)


def _bagian_petugas(r, dari, sampai, dicetak_pada):
    """Susun isi laporan untuk satu petugas."""
    isi = [
        Paragraph("LAPORAN PEMERIKSAAN WARKAH", JUDUL),
        Paragraph(KANTOR, SUBJUDUL),
        Spacer(1, 8),
        _ringkasan(r, dari, sampai),
        Paragraph("A. Kelengkapan berkas", BAGIAN),
    ]

    if r["diperiksa"]:
        isi.append(_kelengkapan(r))
        catatan = _catatan_kelengkapan(r)
        if catatan:
            isi.append(catatan)
    else:
        isi.append(Paragraph("Tidak ada pemeriksaan pada periode ini.", ISI))

    isi.append(Paragraph("B. Desa yang diperiksa", BAGIAN))
    if r["daftar_desa"]:
        baris = [[Paragraph("No", KEPALA), Paragraph("Kecamatan", KEPALA),
                  Paragraph("Desa", KEPALA), Paragraph("Kode desa", KEPALA),
                  Paragraph("Bidang", KEPALA_KANAN), Paragraph("BT ada", KEPALA_KANAN),
                  Paragraph("SU ada", KEPALA_KANAN), Paragraph("Warkah ada", KEPALA_KANAN),
                  Paragraph("Periksa pertama", KEPALA),
                  Paragraph("Periksa terakhir", KEPALA)]]
        for i, d in enumerate(r["daftar_desa"], 1):
            # nama kecamatan/desa dibungkus Paragraph supaya nama panjang
            # turun baris, bukan melebar keluar kolom
            baris.append([str(i), Paragraph(d["nama_kecamatan"], SEL),
                          Paragraph(d["nama_desa"], SEL), d["kode_desa"],
                          angka(d["bidang"]), angka(d["bt_ada"]), angka(d["su_ada"]),
                          angka(d["wk_ada"]), tanggal_pendek(d["tgl_awal"]),
                          tanggal_pendek(d["tgl_akhir"])])
        dokumen = r["dokumen"]
        baris.append(["", "", "JUMLAH", "", angka(r["diperiksa"]),
                      angka(dokumen.get("Buku Tanah", {}).get("Ada", 0)),
                      angka(dokumen.get("Surat Ukur", {}).get("Ada", 0)),
                      angka(dokumen.get("Warkah", {}).get("Ada", 0)), "", ""])
        isi.append(_tabel(
            baris, [9 * mm, 24 * mm, 28 * mm, 18 * mm, 15 * mm, 14 * mm, 14 * mm,
                    17 * mm, 20 * mm, 20 * mm],
            rata_kanan=(4, 5, 6, 7)))
        isi.append(Paragraph(
            "BT = buku tanah, SU = surat ukur. Angka pada ketiga kolom itu adalah "
            "jumlah bidang yang berkasnya ditemukan ada.", KECIL))
    else:
        isi.append(Paragraph("Tidak ada pemeriksaan pada periode ini.", ISI))

    isi.append(Paragraph("C. Rincian status identifikasi", BAGIAN))
    if r["status"]:
        baris = [["Status identifikasi", "Bidang", "Porsi"]]
        for s in r["status"]:
            porsi = (s["bidang"] / r["diperiksa"] * 100) if r["diperiksa"] else 0
            baris.append([Paragraph(s["status_identifikasi"] or "(kosong)", SEL),
                          angka(s["bidang"]), ("%.1f%%" % porsi).replace(".", ",")])
        baris.append(["JUMLAH", angka(r["diperiksa"]), "100,0%"])
        isi.append(_tabel(baris, [90 * mm, 25 * mm, 25 * mm], rata_kanan=(1, 2)))
    else:
        isi.append(Paragraph("Belum ada data.", ISI))

    isi.append(Spacer(1, 16))
    isi.append(KeepTogether(_tanda_tangan(r["nama"], dicetak_pada)))
    return isi


def buat_pdf(daftar, dari=None, sampai=None, dicetak_oleh=None, dicetak_pada=""):
    """Kembalikan isi berkas PDF (bytes) untuk satu atau banyak petugas.

    Tiap petugas mulai di halaman baru supaya laporannya bisa dipisah.
    """
    penampung = BytesIO()
    dok = SimpleDocTemplate(
        penampung, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=20 * mm,
        title="Laporan Pemeriksaan Warkah", author=KANTOR)
    dok.keterangan_kaki = "Dicetak %s%s - Aplikasi Warkah" % (
        dicetak_pada, " oleh %s" % dicetak_oleh if dicetak_oleh else "")

    isi = []
    for i, r in enumerate(daftar):
        if i:
            isi.append(PageBreak())
        isi += _bagian_petugas(r, dari, sampai, dicetak_pada)
    if not isi:
        isi = [Paragraph("Tidak ada data petugas untuk dilaporkan.", ISI)]

    dok.build(isi, onFirstPage=_kaki, onLaterPages=_kaki)
    return penampung.getvalue()


# ------------------------------------------------------------ laporan residu
# Daftar residu PTSL untuk pimpinan: A4 lanskap, ringkasan di halaman pertama,
# lalu rincian per berkas yang dikelompokkan per tahun anggaran.

from xml.sax.saxutils import escape as _esc

from reportlab.lib.pagesizes import landscape
from reportlab.pdfgen import canvas as _canvas

A4_LANSKAP = landscape(A4)
BIRU_MUDA = colors.HexColor("#dbeafe")
SEL_KECIL = ParagraphStyle("sel_kecil", parent=SEL, fontSize=7.5, leading=9.2)
KEPALA_TENGAH = ParagraphStyle("kepala_tengah", parent=KEPALA, alignment=TA_CENTER)
SUBJUDUL_KIRI = ParagraphStyle("subjudul_kiri", parent=ISI, fontName="Helvetica-Bold",
                              textColor=BIRU, spaceAfter=4)


def _p(teks, gaya=SEL_KECIL, kosong="-"):
    """Sel Paragraph dengan teks yang aman untuk markup reportlab."""
    teks = "" if teks is None else str(teks).strip()
    if not teks:
        return Paragraph(kosong, gaya)
    return Paragraph(_esc(teks).replace("\r\n", "\n").replace("\n", "<br/>"), gaya)


def _luas(v):
    try:
        return angka(round(float(v)))
    except (TypeError, ValueError):
        return "-"


class _KanvasBernomor(_canvas.Canvas):
    """Kanvas yang menulis "Halaman x dari y" di kaki tiap halaman.

    Jumlah halaman baru diketahui setelah semua halaman tersusun, jadi setiap
    halaman ditahan dulu lalu kakinya digambar saat berkas disimpan.
    """

    keterangan_kaki = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._halaman = []

    def showPage(self):
        self._halaman.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._halaman)
        for keadaan in self._halaman:
            self.__dict__.update(keadaan)
            lebar = self._pagesize[0]
            self.saveState()
            self.setStrokeColor(ABU)
            self.line(12 * mm, 11 * mm, lebar - 12 * mm, 11 * mm)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(ABU_TUA)
            self.drawString(12 * mm, 7 * mm, self.keterangan_kaki)
            self.drawRightString(lebar - 12 * mm, 7 * mm, "Halaman %d dari %d"
                                 % (self._pageNumber, total))
            self.restoreState()
            super().showPage()
        super().save()


def _tabel_rekap(judul, pasangan, total):
    """Tabel kecil dua kolom (nama, jumlah) + porsi, untuk halaman ringkasan."""
    baris = [[Paragraph(judul, KEPALA), Paragraph("Berkas", KEPALA_KANAN),
              Paragraph("Porsi", KEPALA_KANAN)]]
    for nama, n in pasangan:
        porsi = (n / total * 100) if total else 0
        baris.append([_p(nama, SEL), angka(n), ("%.1f%%" % porsi).replace(".", ",")])
    baris.append(["JUMLAH", angka(total), "100,0%"])
    return _tabel(baris, [50 * mm, 22 * mm, 20 * mm], rata_kanan=(1, 2))


def buat_pdf_residu(data, saringan=(), dicetak_oleh=None, dicetak_pada=""):
    """Kembalikan isi PDF (bytes) daftar residu.

    ``data`` berisi dict dengan kunci tahun, nomor_berkas, nomor_hak, desa,
    kecamatan, pemegang, luas, catatan_baku (daftar kalimat) dan catatan,
    sudah terurut tahun lalu kecamatan, desa, nomor hak.
    ``saringan`` berisi pasangan (label, nilai) saringan yang dipakai.
    """
    penampung = BytesIO()
    dok = SimpleDocTemplate(
        penampung, pagesize=A4_LANSKAP,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=16 * mm,
        title="Daftar Residu PTSL", author=KANTOR)
    kaki = "Dicetak %s%s - Aplikasi Warkah" % (
        dicetak_pada, " oleh %s" % dicetak_oleh if dicetak_oleh else "")

    total = len(data)
    isi = [
        Paragraph("DAFTAR RESIDU PTSL", JUDUL),
        Paragraph(KANTOR, SUBJUDUL),
        Paragraph("Keadaan per %s" % tanggal_id(dicetak_pada[:10]), SUBJUDUL),
        Spacer(1, 8),
    ]

    # keterangan saringan supaya pembaca tahu cakupan datanya
    ket = [["Jumlah berkas", ": %s berkas" % angka(total)]]
    for label, nilai in saringan:
        ket.append([label, ": %s" % nilai])
    t = Table([[Paragraph(_esc(a), ISI), Paragraph(_esc(b), ISI)] for a, b in ket],
              colWidths=[40 * mm, 200 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, ABU),
    ]))
    isi.append(t)

    if not data:
        isi.append(Spacer(1, 12))
        isi.append(Paragraph("Tidak ada data residu untuk saringan ini.", ISI))
        dok.build(isi, canvasmaker=_kanvas(kaki))
        return penampung.getvalue()

    # ---- ringkasan: per tahun dan per kecamatan, berdampingan
    per_tahun, per_kec = {}, {}
    ada_catatan = 0
    for r in data:
        th = r["tahun"] or "(tanpa tahun)"
        per_tahun[th] = per_tahun.get(th, 0) + 1
        kec = r["kecamatan"] or "(tanpa kecamatan)"
        per_kec[kec] = per_kec.get(kec, 0) + 1
        if r["catatan_baku"] or (r["catatan"] or "").strip():
            ada_catatan += 1
    isi.append(Paragraph("A. Ringkasan", BAGIAN))
    kiri = _tabel_rekap("Tahun anggaran", sorted(per_tahun.items()), total)
    kanan = _tabel_rekap("Kecamatan",
                         sorted(per_kec.items(), key=lambda x: (-x[1], x[0])), total)
    dampingan = Table([[kiri, kanan]], colWidths=[100 * mm, 100 * mm], hAlign="LEFT")
    dampingan.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    isi.append(dampingan)
    isi.append(Paragraph(
        "Berkas yang sudah memiliki catatan: <b>%s dari %s berkas</b>." % (
            angka(ada_catatan), angka(total)), KECIL))

    # ---- rincian: satu tabel per tahun; tahun pertama menyambung di bawah
    # ringkasan, tahun berikutnya mulai di halaman baru.
    # Tabel dipecah per tahun juga supaya cepat: reportlab menghitung ulang
    # sisa tabel di tiap halaman, makin panjang tabel makin lambat.
    kolom = ["No", "Tahun", "Nomor Berkas", "Nomor Hak", "Desa", "Kecamatan",
             "Pemegang Hak", "Luas (m²)", "Catatan Baku", "Catatan"]
    lebar = [w * mm for w in (9, 13, 17, 30, 24, 24, 36, 15, 46, 59)]
    kepala = [Paragraph(k, KEPALA_KANAN if k.startswith("Luas") else KEPALA)
              for k in kolom]
    gaya = TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), ABU),
        ("GRID", (0, 0), (-1, -1), 0.4, ABU),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("ALIGN", (7, 1), (7, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f9fafb")]),
    ])

    kelompok = {}
    for r in data:
        kelompok.setdefault(r["tahun"] or "(tanpa tahun)", []).append(r)
    for urut, (th, isi_th) in enumerate(kelompok.items()):
        isi.append(PageBreak() if urut else Spacer(1, 6))
        judul = "B. Rincian berkas residu" if not urut else "B. Rincian berkas residu (lanjutan)"
        isi.append(Paragraph(judul, BAGIAN))
        isi.append(Paragraph("Tahun anggaran %s &nbsp;-&nbsp; %s berkas"
                             % (_esc(th), angka(len(isi_th))), SUBJUDUL_KIRI))
        baris = [kepala]
        for no, r in enumerate(isi_th, 1):
            catatan_baku = "<br/>".join("&bull; " + _esc(c) for c in r["catatan_baku"])
            baris.append([
                # kolom pendek cukup teks biasa; Paragraph hanya untuk yang bisa
                # turun baris, supaya ribuan baris tetap cepat disusun
                str(no), r["tahun"] or "-", r["nomor_berkas"] or "-", _p(r["nomor_hak"]),
                _p(r["desa"]), _p(r["kecamatan"]), _p(r["pemegang"]), _luas(r["luas"]),
                Paragraph(catatan_baku or "-", SEL_KECIL), _p(r["catatan"]),
            ])
        t = LongTable(baris, colWidths=lebar, repeatRows=1, hAlign="LEFT")
        t.setStyle(gaya)
        isi.append(t)

    dok.build(isi, canvasmaker=_kanvas(kaki))
    return penampung.getvalue()


def _kanvas(kaki):
    """Kelas kanvas bernomor yang membawa teks kaki laporan ini."""
    return type("Kanvas", (_KanvasBernomor,), {"keterangan_kaki": kaki})


# ---------------------------------------------- laporan penyerahan residu
NAMA_KELOMPOK = {"hari": "hari", "minggu": "minggu", "bulan": "bulan"}


def _tabel_lebar(baris, lebar, rata_kanan=(), ada_total=True):
    """Tabel landscape: kepala abu, garis tipis, baris total (opsional) tebal."""
    t = LongTable(baris, colWidths=lebar, repeatRows=1, hAlign="LEFT")
    gaya = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), ABU),
        ("GRID", (0, 0), (-1, -1), 0.4, ABU),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f9fafb")]),
    ]
    if ada_total:
        gaya += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
                 ("BACKGROUND", (0, -1), (-1, -1), ABU)]
    for k in rata_kanan:
        gaya.append(("ALIGN", (k, 1), (k, -1), "RIGHT"))
    t.setStyle(TableStyle(gaya))
    return t


def _kepala(*kolom, kanan=()):
    """Baris kepala tabel; kolom bernomor di ``kanan`` rata kanan."""
    return [Paragraph(k, KEPALA_KANAN if i in kanan else KEPALA)
            for i, k in enumerate(kolom)]


def _persen(a, b):
    return ("%.1f%%" % (a / b * 100 if b else 0)).replace(".", ",")


def _rerata(a, b):
    return ("%.1f" % (a / b)).replace(".", ",") if b else "-"


def buat_pdf_serah(h, rincian, saringan=(), kelompok="hari",
                   dicetak_oleh=None, dicetak_pada=""):
    """Kembalikan isi PDF (bytes) laporan penyerahan residu.

    ``h`` adalah hasil app.hitung_serah (ringkas, lingkup, periodik,
    per_petugas, per_desa, label_kel); ``rincian`` daftar sertipikat yang
    diserahkan; ``saringan`` pasangan (label, nilai), yang pertama periodenya.
    """
    penampung = BytesIO()
    dok = SimpleDocTemplate(
        penampung, pagesize=A4_LANSKAP,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=16 * mm,
        title="Laporan Penyerahan Residu PTSL", author=KANTOR)
    kaki = "Dicetak %s%s - Aplikasi Warkah" % (
        dicetak_pada, " oleh %s" % dicetak_oleh if dicetak_oleh else "")

    rk, lk = h["ringkas"], h["lingkup"]
    n = rk["diserahkan"] or 0
    periode = saringan[0][1] if saringan else "Seluruh periode"
    isi = [
        Paragraph("LAPORAN PENYERAHAN SERTIPIKAT RESIDU PTSL", JUDUL),
        Paragraph(KANTOR, SUBJUDUL),
        Paragraph("Periode: %s" % _esc(periode), SUBJUDUL),
        Spacer(1, 8),
    ]

    ket = list(saringan[1:]) + [
        ("Diserahkan pada periode", "%s sertipikat" % angka(n)),
        ("Hari ada penyerahan", "%s hari (rata-rata %s per hari)" % (
            angka(rk["hari"]), _rerata(n, rk["hari"]))),
        ("Tanggal serah tercatat", "%s s/d %s" % (
            tanggal_id(rk["tgl_awal"]), tanggal_id(rk["tgl_akhir"]))
         if rk["tgl_awal"] else "-"),
        ("Posisi seluruh residu", "%s dari %s sudah diserahkan (%s), sisa %s" % (
            angka(lk["sudah"]), angka(lk["jumlah"]), _persen(lk["sudah"], lk["jumlah"]),
            angka(lk["jumlah"] - lk["sudah"]))),
    ]
    t = Table([[Paragraph(_esc(a), ISI), Paragraph(": " + _esc(b), ISI)] for a, b in ket],
              colWidths=[48 * mm, 200 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, ABU),
    ]))
    isi.append(t)
    if lk["tanpa_tanggal"]:
        isi.append(Paragraph(
            "Catatan: %s residu ditandai sudah diserahkan tetapi tanggal serahnya "
            "kosong, sehingga tidak masuk periode mana pun dalam laporan ini; "
            "angkanya tetap terhitung pada posisi seluruh residu."
            % angka(lk["tanpa_tanggal"]), KECIL))

    if not n:
        isi.append(Spacer(1, 12))
        isi.append(Paragraph("Tidak ada penyerahan bertanggal pada saringan ini.", ISI))
        dok.build(isi, canvasmaker=_kanvas(kaki))
        return penampung.getvalue()

    # ---- A. per periode (hari/minggu/bulan sesuai pilihan di halaman)
    per_hari = kelompok == "hari"
    isi.append(Paragraph("A. Penyerahan per %s" % NAMA_KELOMPOK.get(kelompok, "hari"),
                         BAGIAN))
    kolom = [h["label_kel"], "Diserahkan"] + ([] if per_hari else ["Hari"]) + [
        "Desa", "Petugas", "Desa yang diserahkan"]
    kanan = (1, 2) if per_hari else (1, 2, 3)
    baris = [_kepala(*kolom, kanan=kanan)]
    for p in h["periodik"]:
        baris.append([_p(p["label"], SEL), angka(p["diserahkan"])]
                     + ([] if per_hari else [angka(p["hari"])])
                     + [angka(p["desa"]),
                        _p((p["daftar_petugas"] or "").replace(",", ", ")),
                        _p((p["daftar_desa"] or "").replace(",", ", "))])
    baris.append(["JUMLAH", angka(n)] + ([] if per_hari else [angka(rk["hari"])])
                 + [angka(rk["desa"]), "", ""])
    lebar = [50, 22, 16, 55, 130] if per_hari else [68, 22, 14, 16, 46, 107]
    isi.append(_tabel_lebar(baris, [w * mm for w in lebar], rata_kanan=kanan))

    # ---- B. per petugas; judul bagian tidak boleh tertinggal sendirian di
    # dasar halaman, jadi pindah halaman bila sisa ruangnya tinggal sedikit
    isi.append(CondPageBreak(40 * mm))
    isi.append(Paragraph("B. Penyerahan per petugas", BAGIAN))
    baris = [_kepala("Petugas", "Diserahkan", "Porsi", "Hari", "Rata-rata/hari",
                     "Desa", "Pertama", "Terakhir", kanan=(1, 2, 3, 4, 5))]
    for p in h["per_petugas"]:
        baris.append([_p(p["nama"], SEL), angka(p["diserahkan"]),
                      _persen(p["diserahkan"], n), angka(p["hari"]),
                      _rerata(p["diserahkan"], p["hari"]), angka(p["desa"]),
                      tanggal_id(p["tgl_awal"]), tanggal_id(p["tgl_akhir"])])
    baris.append(["JUMLAH", angka(n), "100,0%", angka(rk["hari"]),
                  _rerata(n, rk["hari"]), angka(rk["desa"]), "", ""])
    isi.append(_tabel_lebar(baris, [w * mm for w in (60, 24, 20, 16, 26, 16, 34, 34)],
                            rata_kanan=(1, 2, 3, 4, 5)))

    # ---- C. per desa, dengan progres seluruh residu desa itu
    isi.append(CondPageBreak(40 * mm))
    isi.append(Paragraph("C. Penyerahan per desa", BAGIAN))
    isi.append(Paragraph(
        "Total residu, sudah diserahkan, dan sisa menghitung seluruh residu aktif "
        "di desa tersebut tanpa batas periode.", KECIL))
    isi.append(Spacer(1, 3))
    baris = [_kepala("Kecamatan", "Desa", "Diserahkan pada periode", "Total residu",
                     "Sudah diserahkan", "Sisa", "Progres", "Terakhir diserahkan",
                     kanan=(2, 3, 4, 5, 6))]
    jml_n = jml_total = jml_sudah = 0
    for d in h["per_desa"]:
        jml_n += d["diserahkan"]
        jml_total += d["total"]
        jml_sudah += d["sudah"]
        baris.append([_p(d["kecamatan"], SEL), _p(d["desa"], SEL),
                      angka(d["diserahkan"]), angka(d["total"]), angka(d["sudah"]),
                      angka(d["total"] - d["sudah"]), _persen(d["sudah"], d["total"]),
                      tanggal_id(d["tgl_akhir"])])
    baris.append(["JUMLAH %d DESA" % len(h["per_desa"]), "", angka(jml_n),
                  angka(jml_total), angka(jml_sudah), angka(jml_total - jml_sudah),
                  _persen(jml_sudah, jml_total), ""])
    isi.append(_tabel_lebar(baris, [w * mm for w in (40, 46, 30, 24, 28, 20, 22, 36)],
                            rata_kanan=(2, 3, 4, 5, 6)))

    # ---- D. daftar sertipikat, mulai di halaman baru
    isi.append(PageBreak())
    isi.append(Paragraph("D. Daftar sertipikat yang diserahkan (%s)" % angka(n), BAGIAN))
    baris = [_kepala("No", "Tanggal Serah", "Tahun", "Nomor Berkas", "Nomor Hak",
                     "Pemegang Hak", "Penerima", "Desa", "Kecamatan", "Petugas")]
    for no, r in enumerate(rincian, 1):
        # kolom pendek cukup teks biasa supaya ribuan baris tetap cepat disusun
        baris.append([str(no), tanggal_pendek(r["tanggal"]), r["tahun"] or "-",
                      r["nomor_berkas"] or "-", _p(r["nomor_hak"]),
                      _p(r["nama_pemegang"]), _p(r["penerima"]), _p(r["desa"]),
                      _p(r["kecamatan"]), _p(r["nama"])])
    isi.append(_tabel_lebar(baris, [w * mm for w in (9, 20, 13, 19, 32, 42, 42, 30, 28, 37)],
                            ada_total=False))

    dok.build(isi, canvasmaker=_kanvas(kaki))
    return penampung.getvalue()


# ------------------------------------------------- rekap tipologi residu
def _nol(n):
    """Angka sel rekap; nol ditulis "-" supaya sel berisi mudah terlihat."""
    return angka(n) if n else "-"


def buat_pdf_tipologi(rk, saringan=(), tanpa="(tanpa tipologi)",
                      dicetak_oleh=None, dicetak_pada=""):
    """Kembalikan isi PDF (bytes) rekap tipologi residu belum diserahkan.

    ``rk`` adalah hasil app.hitung_rekap_tipologi; ``saringan`` pasangan
    (label, nilai) saringan wilayah yang sedang terpasang.
    """
    penampung = BytesIO()
    dok = SimpleDocTemplate(
        penampung, pagesize=A4_LANSKAP,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=16 * mm,
        title="Rekap Tipologi Residu PTSL Belum Diserahkan", author=KANTOR)
    kaki = "Dicetak %s%s - Aplikasi Warkah" % (
        dicetak_pada, " oleh %s" % dicetak_oleh if dicetak_oleh else "")
    lebar_isi = A4_LANSKAP[0] - 24 * mm
    label = lambda k: "Tanpa" if k == tanpa else k
    jumlah = rk["jumlah"]

    isi = [
        Paragraph("REKAP TIPOLOGI RESIDU PTSL BELUM DISERAHKAN", JUDUL),
        Paragraph(KANTOR, SUBJUDUL),
        Spacer(1, 8),
    ]
    ket = list(saringan) + [
        ("Cakupan", "Residu aktif yang belum diserahkan (berkas tidak ditemukan "
                    "tidak ikut)"),
        ("Jumlah berkas", "%s berkas dalam %s tahun anggaran" % (
            angka(jumlah), len(rk["tahun"]))),
        ("Kelompok terbanyak", "%s (%s berkas, %s)" % (
            rk["terbanyak"], angka(rk["total_kel"][rk["terbanyak"]]),
            _persen(rk["total_kel"][rk["terbanyak"]], jumlah))
         if rk["terbanyak"] and jumlah else "-"),
        ("Tanpa tipologi", "%s berkas" % angka(rk["total_kel"].get(tanpa, 0))),
        ("Berkas > 1 kelompok", "%s berkas" % angka(rk["ganda"])),
    ]
    t = Table([[Paragraph(_esc(a), ISI), Paragraph(": " + _esc(b), ISI)] for a, b in ket],
              colWidths=[48 * mm, 200 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, ABU),
    ]))
    isi.append(t)

    if not jumlah:
        isi.append(Spacer(1, 12))
        isi.append(Paragraph("Tidak ada residu belum diserahkan pada saringan ini.", ISI))
        dok.build(isi, canvasmaker=_kanvas(kaki))
        return penampung.getvalue()

    # ---- A. tahun x kelompok
    isi.append(Paragraph("A. Per kelompok tipologi dan tahun", BAGIAN))
    isi.append(Paragraph(
        "Berkas yang membawa kode dari lebih dari satu kelompok terhitung di tiap "
        "kelompoknya, jadi jumlah kolom kelompok bisa melebihi Jumlah berkas.", KECIL))
    isi.append(Spacer(1, 3))
    kel = rk["kelompok"]
    kanan = tuple(range(1, len(kel) + 2))
    baris = [_kepala("Tahun", *[label(k) for k in kel], "Jumlah berkas", kanan=kanan)]
    for th in rk["tahun"]:
        d = rk["per_kel"].get(th, {})
        baris.append([th] + [_nol(d.get(k, 0)) for k in kel] + [angka(rk["berkas"][th])])
    baris.append(["% dari berkas"] + [_persen(rk["total_kel"][k], jumlah) for k in kel]
                 + ["100,0%"])
    baris.append(["SELURUH TAHUN"] + [_nol(rk["total_kel"][k]) for k in kel]
                 + [angka(jumlah)])
    w_tahun, w_jumlah = 30 * mm, 26 * mm
    w_sel = min(22 * mm, (lebar_isi - w_tahun - w_jumlah) / len(kel))
    tbl = _tabel_lebar(baris, [w_tahun] + [w_sel] * len(kel) + [w_jumlah],
                       rata_kanan=kanan)
    tbl.setStyle(TableStyle([
        ("FONT", (-1, 1), (-1, -1), "Helvetica-Bold", 8),
        ("TEXTCOLOR", (0, -2), (-1, -2), ABU_TUA),
        ("FONT", (0, -2), (-1, -2), "Helvetica-Oblique", 7.5),
    ]))
    isi.append(tbl)

    # ---- B. kode x tahun, dengan subtotal per kelompok
    isi.append(CondPageBreak(60 * mm))
    isi.append(Paragraph("B. Rincian per kode tipologi", BAGIAN))
    isi.append(Paragraph("Baris berlatar abu adalah subtotal kelompok; kolom paling "
                         "kanan jumlah seluruh tahun.", KECIL))
    isi.append(Spacer(1, 3))
    thn = rk["tahun"]
    kanan = tuple(range(2, len(thn) + 3))
    baris = [_kepala("Kode", "Keterangan", *thn, "Seluruh tahun", kanan=kanan)]
    subtotal = []
    for g in kel:
        anak = [k for k in rk["kode"] if rk["kel_dari_kode"].get(k) == g and k != g]
        if anak:
            subtotal.append(len(baris))
        ketg = ("Tipologi belum diisi" if g == tanpa
                else rk["nama"].get(g) or ("Kelompok %s" % g if anak else ""))
        d = {th: rk["per_kel"].get(th, {}).get(g, 0) for th in thn}
        baris.append([label(g), _p(ketg, kosong="")] + [_nol(d[th]) for th in thn]
                     + [_nol(rk["total_kel"][g])])
        for k in anak:
            dk = rk["per_kode"].get(k, {})
            baris.append(["   " + k, _p(rk["nama"].get(k), kosong="")]
                         + [_nol(dk.get(th, 0)) for th in thn]
                         + [_nol(rk["total_kode"][k])])
    baris.append(["JUMLAH BERKAS", ""] + [angka(rk["berkas"][th]) for th in thn]
                 + [angka(jumlah)])
    w_kode, w_ket, w_total = 22 * mm, 46 * mm, 24 * mm
    w_sel = min(20 * mm, (lebar_isi - w_kode - w_ket - w_total) / max(len(thn), 1))
    tbl = _tabel_lebar(baris, [w_kode, w_ket] + [w_sel] * len(thn) + [w_total],
                       rata_kanan=kanan)
    gaya = [("FONT", (-1, 1), (-1, -1), "Helvetica-Bold", 8)]
    for i in subtotal:
        gaya += [("FONT", (0, i), (-1, i), "Helvetica-Bold", 8),
                 ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f3f4f6"))]
    tbl.setStyle(TableStyle(gaya))
    isi.append(tbl)

    dok.build(isi, canvasmaker=_kanvas(kaki))
    return penampung.getvalue()
