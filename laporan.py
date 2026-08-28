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
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

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
