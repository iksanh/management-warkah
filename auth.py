"""Pengelolaan sandi dan hak akses pengguna."""
import base64
import hashlib
import hmac
import os
import secrets
import string

ITERASI = 200_000
ALGORITMA = "pbkdf2_sha256"

# urutan menentukan pilihan bawaan pada borang tambah akun
PERAN = ["Petugas", "Admin"]


def buat_sandi(sandi: str) -> str:
    """Ubah sandi polos menjadi simpanan berformat algoritma$iterasi$garam$hash."""
    garam = os.urandom(16)
    kunci = hashlib.pbkdf2_hmac("sha256", sandi.encode("utf-8"), garam, ITERASI)
    return "%s$%d$%s$%s" % (ALGORITMA, ITERASI,
                            base64.b64encode(garam).decode(),
                            base64.b64encode(kunci).decode())


def cek_sandi(sandi: str, simpanan: str) -> bool:
    """Cocokkan sandi polos dengan simpanan; aman terhadap serangan waktu."""
    if not simpanan:
        return False
    try:
        algoritma, iterasi, garam_b64, kunci_b64 = simpanan.split("$")
        if algoritma != ALGORITMA:
            return False
        garam = base64.b64decode(garam_b64)
        kunci = base64.b64decode(kunci_b64)
    except (ValueError, TypeError):
        return False
    uji = hashlib.pbkdf2_hmac("sha256", sandi.encode("utf-8"), garam, int(iterasi))
    return hmac.compare_digest(uji, kunci)


def sandi_acak(panjang: int = 10) -> str:
    """Sandi awal yang mudah dibacakan: tanpa huruf/angka yang mirip."""
    huruf = "abcdefghjkmnpqrstuvwxyz"
    angka = "23456789"
    kolam = huruf + huruf.upper() + angka
    return "".join(secrets.choice(kolam) for _ in range(panjang))


def kunci_rahasia(berkas: str) -> str:
    """Kunci penanda tangan cookie sesi; dibuat sekali lalu dipakai terus."""
    if os.path.exists(berkas):
        with open(berkas, "r", encoding="utf-8") as f:
            nilai = f.read().strip()
            if nilai:
                return nilai
    nilai = secrets.token_urlsafe(48)
    with open(berkas, "w", encoding="utf-8") as f:
        f.write(nilai)
    return nilai


def nama_pengguna_dari(nama: str) -> str:
    """Usulkan nama pengguna dari nama orang: huruf kecil tanpa spasi."""
    sah = string.ascii_lowercase + string.digits
    return "".join(c for c in nama.lower().replace(" ", "") if c in sah) or "pengguna"
