"""Pengelolaan akun dari terminal.

    python kelola_pengguna.py awal                     buat akun admin + 4 petugas
    python kelola_pengguna.py daftar                   tampilkan semua akun
    python kelola_pengguna.py tambah "Nama" username [Admin|Petugas]
    python kelola_pengguna.py sandi username           atur ulang sandi (acak)
    python kelola_pengguna.py peran username Admin     ubah peran
    python kelola_pengguna.py nonaktif username        matikan akun
    python kelola_pengguna.py aktif username           hidupkan akun
"""
import sys
from datetime import datetime

import auth
import db


def sekarang():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def daftar(kon):
    baris = kon.execute(
        "SELECT id, nama, username, peran, aktif, terakhir_masuk, sandi "
        "FROM petugas ORDER BY peran, nama").fetchall()
    print("%-4s %-18s %-14s %-9s %-8s %-8s %s"
          % ("ID", "NAMA", "USERNAME", "PERAN", "AKTIF", "SANDI", "TERAKHIR MASUK"))
    for r in baris:
        print("%-4s %-18s %-14s %-9s %-8s %-8s %s"
              % (r["id"], r["nama"], r["username"] or "-", r["peran"],
                 "ya" if r["aktif"] else "tidak",
                 "ada" if r["sandi"] else "belum", r["terakhir_masuk"] or "-"))


def atur_sandi(kon, username, sandi=None):
    r = kon.execute("SELECT id, nama FROM petugas WHERE username = ?", (username,)).fetchone()
    if not r:
        print("Akun '%s' tidak ada." % username)
        return None
    sandi = sandi or auth.sandi_acak()
    kon.execute("UPDATE petugas SET sandi = ?, sandi_diubah_pada = ? WHERE id = ?",
                (auth.buat_sandi(sandi), sekarang(), r["id"]))
    kon.commit()
    return sandi


def tambah(kon, nama, username, peran="Petugas"):
    if peran not in auth.PERAN:
        print("Peran harus salah satu dari:", ", ".join(auth.PERAN))
        return
    ada = kon.execute("SELECT id FROM petugas WHERE nama = ?", (nama,)).fetchone()
    sandi = auth.sandi_acak()
    if ada:
        kon.execute("UPDATE petugas SET username=?, sandi=?, peran=?, aktif=1, "
                    "sandi_diubah_pada=? WHERE id=?",
                    (username, auth.buat_sandi(sandi), peran, sekarang(), ada["id"]))
    else:
        kon.execute("INSERT INTO petugas (nama, username, sandi, peran, aktif, dibuat_pada, "
                    "sandi_diubah_pada) VALUES (?,?,?,?,1,?,?)",
                    (nama, username, auth.buat_sandi(sandi), peran, sekarang(), sekarang()))
    kon.commit()
    print("  %-18s %-14s %-9s sandi: %s" % (nama, username, peran, sandi))


def awal(kon):
    print("Membuat akun awal. CATAT sandi di bawah ini — hanya ditampilkan sekali.\n")
    tambah(kon, "Administrator", "admin", "Admin")
    for nama in db.PETUGAS_AWAL:
        tambah(kon, nama, auth.nama_pengguna_dari(nama), "Petugas")
    print("\nSegera ganti sandi lewat menu 'Ganti sandi' di dalam aplikasi.")


def main(argv):
    kon = db.siapkan()
    perintah = argv[1] if len(argv) > 1 else "daftar"

    if perintah == "awal":
        awal(kon)
    elif perintah == "daftar":
        daftar(kon)
    elif perintah == "tambah" and len(argv) >= 4:
        tambah(kon, argv[2], argv[3], argv[4] if len(argv) > 4 else "Petugas")
    elif perintah == "sandi" and len(argv) >= 3:
        baru = atur_sandi(kon, argv[2], argv[3] if len(argv) > 3 else None)
        if baru:
            print("Sandi baru untuk %s: %s" % (argv[2], baru))
    elif perintah == "peran" and len(argv) >= 4:
        if argv[3] not in auth.PERAN:
            print("Peran harus salah satu dari:", ", ".join(auth.PERAN))
        else:
            kon.execute("UPDATE petugas SET peran=? WHERE username=?", (argv[3], argv[2]))
            kon.commit()
            print("Peran %s diubah menjadi %s." % (argv[2], argv[3]))
    elif perintah in ("aktif", "nonaktif") and len(argv) >= 3:
        kon.execute("UPDATE petugas SET aktif=? WHERE username=?",
                    (1 if perintah == "aktif" else 0, argv[2]))
        kon.commit()
        print("Akun %s sekarang %s." % (argv[2], perintah))
    else:
        print(__doc__)
    kon.close()


if __name__ == "__main__":
    main(sys.argv)
