# Panduan Deploy Aplikasi Warkah — AWS EC2 (Jakarta) + Ubuntu 26.04 + Jenkins CI/CD + GitHub

Panduan ini memasang **Aplikasi Warkah** (Starlette + Uvicorn + SQLite) di **AWS EC2
region Asia Pacific (Jakarta) — `ap-southeast-3`**, di atas Ubuntu Server 26.04, lengkap
dengan pipeline **Jenkins** yang otomatis menarik kode dari **GitHub**, mengujinya, lalu
men-deploy-nya dengan mekanisme *release + symlink* (bisa di-*rollback* dalam hitungan
detik).

Perintah `aws ...` dijalankan dari komputer kerja (AWS CLI v2). Perintah lain dijalankan
di dalam instance EC2 sebagai pengguna `ubuntu` yang punya hak `sudo`.

---

## Daftar Isi

1. [Arsitektur & prasyarat](#1-arsitektur--prasyarat)
2. [Hal penting khusus aplikasi ini](#2-hal-penting-khusus-aplikasi-ini)
3. [Menyiapkan infrastruktur AWS Jakarta](#3-menyiapkan-infrastruktur-aws-jakarta)
4. [Menyiapkan repositori GitHub](#4-menyiapkan-repositori-github)
5. [Persiapan sistem di EC2](#5-persiapan-sistem-di-ec2)
6. [Volume data & struktur direktori](#6-volume-data--struktur-direktori)
7. [Deploy manual pertama kali](#7-deploy-manual-pertama-kali)
8. [Service systemd](#8-service-systemd)
9. [Nginx, domain, dan HTTPS](#9-nginx-domain-dan-https)
10. [Memasang Jenkins](#10-memasang-jenkins)
11. [Kredensial & akses Jenkins ke server](#11-kredensial--akses-jenkins-ke-server)
12. [Script deploy di server](#12-script-deploy-di-server)
13. [Jenkinsfile (pipeline CI/CD)](#13-jenkinsfile-pipeline-cicd)
14. [Membuat job di Jenkins](#14-membuat-job-di-jenkins)
15. [Webhook GitHub](#15-webhook-github)
16. [Rollback](#16-rollback)
17. [Cadangan: S3 + EBS snapshot](#17-cadangan-s3--ebs-snapshot)
18. [Impor data KKP di server](#18-impor-data-kkp-di-server)
19. [Pengamanan AWS](#19-pengamanan-aws)
20. [Perkiraan biaya](#20-perkiraan-biaya)
21. [Pemeliharaan & pemecahan masalah](#21-pemeliharaan--pemecahan-masalah)
22. [Daftar periksa](#22-daftar-periksa)

---

## 1. Arsitektur & prasyarat

```
  Komputer kerja          GitHub                AWS ap-southeast-3 (Jakarta)
 ┌──────────────┐    ┌──────────────┐   ┌────────────────────────────────────────┐
 │  git push    │──► │ repo warkah  │   │  VPC default · Subnet publik · AZ-a    │
 └──────────────┘    └──────┬───────┘   │ ┌────────────────────────────────────┐ │
                            │ webhook   │ │  EC2 t3.medium  (Elastic IP)       │ │
                            │ HTTPS 443 │ │                                    │ │
                            └──────────►│ │  Nginx :443                        │ │
                                        │ │    ├── /         → uvicorn :8000   │ │
                                        │ │    └── /jenkins/ → jenkins :8080   │ │
                                        │ │                                    │ │
                                        │ │  Jenkins ─ssh─► deploy.sh          │ │
                                        │ │  /opt/warkah/current → releases/N  │ │
                                        │ │                                    │ │
                                        │ │  EBS root gp3 30 GB   (/)          │ │
                                        │ │  EBS data gp3 20 GB   (/var/lib/   │ │
                                        │ │      warkah → warkah.db)           │ │
                                        │ └──────────────┬─────────────────────┘ │
                                        │                │ cadangan harian       │
                                        │                ▼                       │
                                        │   S3 warkah-cadangan-<akun>            │
                                        │   + EBS snapshot (DLM)                 │
                                        └────────────────────────────────────────┘
```

Yang dibutuhkan:

| Komponen | Nilai yang dipakai panduan ini |
|---|---|
| Region | `ap-southeast-3` (Jakarta) |
| Instance | `t3.medium` (2 vCPU, 4 GB) — aplikasi **dan** Jenkins satu mesin |
| AMI | Ubuntu Server 26.04 LTS (Canonical), arsitektur `x86_64` |
| Penyimpanan | Root `gp3` 30 GB + volume data `gp3` 20 GB terpisah |
| Alamat IP | Elastic IP (agar tidak berubah saat instance di-*restart*) |
| GitHub | Repositori **privat** — ini data pertanahan |
| Domain | Sangat dianjurkan (mis. `warkah.contoh.go.id`) agar bisa Let's Encrypt |

**Kenapa `t3.medium`, bukan `t3.small`?** Jenkins (JVM) + Uvicorn + Nginx pada 2 GB akan
sering kehabisan memori saat tahap `pip install`. Bila anggaran ketat, `t3.small` bisa
dipakai dengan swap 2 GB (bagian 5.4), atau Jenkins dipindah ke instance sendiri —
`Jenkinsfile` sudah disiapkan untuk keduanya (bagian 13).

**Kenapa volume data terpisah?** `warkah.db` bisa di-*snapshot* dan dipulihkan tanpa
menyentuh sistem, dan tetap utuh walaupun instance diganti total.

---

## 2. Hal penting khusus aplikasi ini

Baca bagian ini lebih dulu — lima hal berikut adalah sumber kesalahan paling sering.

**a. `warkah.db` tidak boleh ikut ter-deploy.**
Basis data berisi seluruh data kerja (penyimpanan, pemeriksaan, peminjaman, penugasan).
Bila ikut disalin dari repo, data lapangan hilang tertimpa. Basis data disimpan di
`/var/lib/warkah/warkah.db` (volume EBS terpisah), di luar folder rilis.

Letaknya ditunjuk lewat peubah lingkungan pada unit systemd, tanpa perlu mengubah
isi `db.py`:

```ini
Environment=WARKAH_DB=/var/lib/warkah/warkah.db
```

Bila `WARKAH_DB` tidak diisi, `db.py` memakai `warkah.db` di samping `app.py` seperti
saat dijalankan di komputer sendiri. Cara lama — **symlink** `warkah.db` ke tiap rilis —
masih berlaku dan boleh dipakai bersamaan; bila keduanya ada, `WARKAH_DB` yang menang.
Folder tujuannya harus sudah ada: `db.siapkan()` berhenti dengan pesan yang menyebut
`WARKAH_DB` bila foldernya tidak ditemukan, supaya salah tulis ketahuan saat deploy
dan bukan berupa galat `unable to open database file` yang membingungkan.

**b. `rahasia.txt` juga data, bukan kode.**
Berkas ini kunci penanda tangan cookie sesi (`auth.kunci_rahasia`, dipakai di
`app.py:701`). Bila isinya berganti, semua pengguna terlempar keluar. Simpan sekali di
`/var/lib/warkah/rahasia.txt`. `rahasia.txt` yang ada di komputer sekarang **jangan
dibawa ke server** — buat yang baru di server.

**c. Skema basis data hanya dibuat saat `python app.py`.**
Di `app.py`, `db.siapkan()` berada di dalam blok `if __name__ == "__main__":`. Saat
dijalankan lewat Uvicorn dengan `app:app`, blok itu **tidak dieksekusi**, sehingga tabel
baru atau kolom tambahan tidak pernah dibuat. Karena itu setiap deploy menjalankan
migrasi eksplisit:

```bash
python -c "import db; db.siapkan().close()"
```

Perintah itu idempoten (aman diulang) dan sudah dipasang di `deploy.sh` maupun di
`ExecStartPre` systemd.

**d. Salinan berkas unggahan ikut folder rilis bila tidak diarahkan.**
Menu **Impor** menyimpan salinan berkas yang diunggah ke folder `unggahan/` di samping
`app.py`, sehingga akan hilang begitu rilis diganti. Arahkan ke volume data lewat peubah
lingkungan pada unit systemd:

```ini
Environment=WARKAH_UNGGAHAN=/var/lib/warkah/unggahan
```

Buat foldernya sekali dan beri kepemilikan ke pengguna layanan:

```bash
sudo mkdir -p /var/lib/warkah/unggahan
sudo chown warkah:warkah /var/lib/warkah/unggahan
```

Folder ini hanya arsip penelusuran; aplikasi tidak pernah membacanya kembali, jadi aman
dibersihkan berkala.

**e. SQLite dengan beberapa worker.**
`db.sambung()` memakai `timeout=30` dan skema mengaktifkan `PRAGMA journal_mode = WAL`,
jadi 2 worker aman untuk skala kantor. **Jangan** pindahkan `warkah.db` ke EFS atau
berbagi jaringan lain — WAL tidak andal di sana; EBS (blok, satu instance) justru tepat.
Bila muncul `database is locked` berulang, turunkan ke `--workers 1`.

---

## 3. Menyiapkan infrastruktur AWS Jakarta

### 3.1 AWS CLI di komputer kerja

```powershell
aws --version
aws configure
# AWS Access Key ID / Secret
# Default region name: ap-southeast-3
# Default output format: json

aws sts get-caller-identity            # pastikan akun benar
aws ec2 describe-availability-zones --region ap-southeast-3 --output table
```

Seluruh perintah di bawah memakai region Jakarta. Setel sekali agar tidak perlu diulang:

```powershell
$env:AWS_REGION = "ap-southeast-3"
```

### 3.2 Key pair SSH

```powershell
aws ec2 create-key-pair --key-name warkah-key `
  --query "KeyMaterial" --output text | Out-File -Encoding ascii $HOME\.ssh\warkah-key.pem
```

Amankan izin berkasnya (Windows):

```powershell
icacls "$HOME\.ssh\warkah-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

> Simpan `.pem` ini baik-baik. AWS tidak menyimpan salinannya — hilang berarti harus
> membuat key pair baru dan memasangnya lewat Session Manager (bagian 19.3).

### 3.3 Security Group

Dua aturan masuk saja: SSH dari kantor, dan HTTPS/HTTP dari mana pun (dibutuhkan
Let's Encrypt dan webhook GitHub).

```powershell
$VPC = aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text

$SG = aws ec2 create-security-group --group-name warkah-sg `
  --description "Aplikasi Warkah - Kantah Bone Bolango" `
  --vpc-id $VPC --query "GroupId" --output text
echo $SG

# IP publik kantor — ganti dengan milik Anda (cek: curl ifconfig.me)
$KANTOR = "103.xxx.xxx.xxx/32"

aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 22  --cidr $KANTOR
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 80  --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 443 --cidr 0.0.0.0/0
```

| Port | Sumber | Alasan |
|---|---|---|
| 22 | IP kantor saja | SSH administrasi |
| 80 | `0.0.0.0/0` | Verifikasi Let's Encrypt + pengalihan ke HTTPS |
| 443 | `0.0.0.0/0` | Aplikasi, dan webhook GitHub lewat `/jenkins/` |

**Port 8080 sengaja tidak dibuka.** Jenkins diakses lewat Nginx di
`https://<domain>/jenkins/` (bagian 10.1), sehingga tidak ada antarmuka Jenkins yang
telanjang di internet.

> **Aplikasi hanya untuk pegawai kantor?** Batasi juga port 443 ke IP kantor. Tapi ingat:
> webhook GitHub berasal dari IP GitHub, jadi izinkan juga rentangnya —
> `curl -s https://api.github.com/meta` bagian `hooks`. Bila terlalu repot, lewati
> webhook dan pakai *polling* (bagian 15).

### 3.4 IAM role untuk instance

Agar server bisa menulis cadangan ke S3 dan diakses lewat Session Manager **tanpa
menyimpan access key di dalam server**.

`kepercayaan.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

```powershell
aws iam create-role --role-name warkah-ec2-role `
  --assume-role-policy-document file://kepercayaan.json

aws iam attach-role-policy --role-name warkah-ec2-role `
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

aws iam create-instance-profile --instance-profile-name warkah-ec2-profile
aws iam add-role-to-instance-profile --instance-profile-name warkah-ec2-profile `
  --role-name warkah-ec2-role
```

Kebijakan S3 ditambahkan setelah bucket dibuat (bagian 17.1).

### 3.5 Menemukan AMI Ubuntu 26.04

```powershell
aws ec2 describe-images --region ap-southeast-3 `
  --owners 099720109477 `
  --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-*-26.04-amd64-server-*" `
            "Name=state,Values=available" `
  --query "sort_by(Images,&CreationDate)[-1].[ImageId,Name]" --output text
```

> Bila belum ada hasil (AMI 26.04 belum terbit di region ini saat Anda menjalankannya),
> ganti `26.04` dengan `24.04` — seluruh isi panduan ini berlaku sama untuk 24.04 LTS.
> Jangan memakai AMI dari pihak ketiga di AWS Marketplace untuk data pertanahan.

### 3.6 Membuat instance

```powershell
$AMI = "ami-xxxxxxxxxxxx"      # hasil langkah 3.5
$SUBNET = aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC" `
  "Name=availability-zone,Values=ap-southeast-3a" --query "Subnets[0].SubnetId" --output text

aws ec2 run-instances `
  --region ap-southeast-3 `
  --image-id $AMI `
  --instance-type t3.medium `
  --key-name warkah-key `
  --security-group-ids $SG `
  --subnet-id $SUBNET `
  --iam-instance-profile Name=warkah-ec2-profile `
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" `
  --block-device-mappings '[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":30,\"VolumeType\":\"gp3\",\"Encrypted\":true,\"DeleteOnTermination\":true}}]' `
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=warkah-prod},{Key=Aplikasi,Value=Warkah},{Key=Lingkungan,Value=produksi}]' `
  --query "Instances[0].InstanceId" --output text
```

`HttpTokens=required` mewajibkan IMDSv2 — melindungi kredensial IAM instance dari
serangan SSRF.

### 3.7 Volume data terpisah

```powershell
$IID = "i-xxxxxxxxxxxx"

$VOL = aws ec2 create-volume --availability-zone ap-southeast-3a `
  --size 20 --volume-type gp3 --encrypted `
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=warkah-data}]' `
  --query "VolumeId" --output text

aws ec2 attach-volume --volume-id $VOL --instance-id $IID --device /dev/sdf
```

### 3.8 Elastic IP

```powershell
$EIP = aws ec2 allocate-address --domain vpc --query "AllocationId" --output text
aws ec2 associate-address --instance-id $IID --allocation-id $EIP

aws ec2 describe-instances --instance-ids $IID `
  --query "Reservations[0].Instances[0].PublicIpAddress" --output text
```

Catat alamatnya — itu yang dipakai untuk SSH, DNS, dan webhook.

### 3.9 Masuk ke server

```powershell
ssh -i $HOME\.ssh\warkah-key.pem ubuntu@<ELASTIC-IP>
```

---

## 4. Menyiapkan repositori GitHub

Dijalankan **di komputer kerja**, di dalam folder `app_warkah`.

### 4.1 Berkas `.gitignore`

```gitignore
# data & rahasia — TIDAK BOLEH masuk repositori
warkah.db
warkah.db-wal
warkah.db-shm
rahasia.txt
unggahan/
*.xlsx
*.csv

# kredensial AWS
*.pem
kepercayaan.json

# berkas sementara
__pycache__/
*.py[cod]
.venv/
venv/
server.log
*.log
```

### 4.2 Berkas `requirements.txt`

Versi diambil dari lingkungan yang sekarang sudah berjalan:

```text
starlette==1.1.0
uvicorn==0.47.0
Jinja2==3.1.6
itsdangerous==2.2.0
python-multipart==0.0.29
reportlab==5.0.1
pandas==3.0.3
openpyxl==3.1.5
```

`reportlab` dipakai `laporan.py` untuk mencetak laporan pemeriksaan per petugas ke PDF
(rute `/monitoring/laporan.pdf`). Paketnya murni Python, tidak perlu pustaka sistem
tambahan.

`pandas` dan `openpyxl` **wajib ada di server**, bukan lagi hanya untuk pekerjaan di
komputer kerja: `app.py` memuat `impor.py` yang membaca berkas Excel/CSV unggahan pada
menu **Impor**. Tanpa keduanya aplikasi gagal start dengan `ModuleNotFoundError:
No module named 'pandas'`. `python-multipart` juga wajib — Starlette memakainya untuk
membaca unggahan berkas.

### 4.3 Berkas `requirements-data.txt`

Hanya dipakai skrip pengolahan data di komputer kerja (`import_data.py`,
`merge_per_folder.py`, dan kawan-kawan). Isinya kini sudah tercakup
`requirements.txt`, berkas ini dipertahankan supaya skrip lama tetap punya rujukan:

```text
pandas==3.0.3
openpyxl==3.1.5
```

### 4.4 Inisialisasi dan unggah

```bash
git init -b main
git add .
git commit -m "Aplikasi Warkah - versi awal"

# pastikan tidak ada data atau kunci yang ikut:
git ls-files | grep -E "warkah.db|rahasia.txt|xlsx|\.pem"   # harus kosong

git remote add origin git@github.com:<akun>/app-warkah.git
git push -u origin main
```

> Bila `warkah.db` atau berkas `.pem` telanjur ter-*commit*, menghapusnya di commit
> berikutnya tidak cukup — berkas tetap tersimpan di riwayat. Buat repositori baru, atau
> bersihkan dengan `git filter-repo --path warkah.db --invert-paths` lalu *force push*.
> Bila `.pem` yang bocor, **buat key pair baru** di AWS dan cabut yang lama.

### 4.5 Cabang

| Cabang | Fungsi |
|---|---|
| `main` | Kode yang berjalan di produksi. Hanya di-*merge* dari `dev` setelah pipeline hijau |
| `dev` | Pengembangan harian. Pipeline menguji tapi **tidak** men-deploy |

---

## 5. Persiapan sistem di EC2

### 5.1 Pembaruan dasar & zona waktu

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nginx sqlite3 curl rsync unzip

# EC2 bawaannya UTC. Kantah Bone Bolango berada di WITA:
sudo timedatectl set-timezone Asia/Makassar
date
python3 -V        # aplikasi jalan di Python 3.11 ke atas
```

> Region AWS-nya Jakarta, tetapi zona waktu **operator**-lah yang menentukan. Bone
> Bolango = WITA (`Asia/Makassar`, UTC+8). Bila petugas yang memakai aplikasi berada di
> WIB, ganti ke `Asia/Jakarta`. Nilai ini ikut memengaruhi kolom waktu di log aktivitas,
> tanggal pinjam, dan tenggat 14 hari (`LAMA_PINJAM_HARI` di `app.py`).

> Ubuntu 26.04 menerapkan PEP 668 (*externally-managed environment*): `pip install`
> langsung ke Python sistem akan ditolak. Semua dependensi dipasang di **virtualenv**
> (`/opt/warkah/venv`), sesuai panduan ini.

### 5.2 AWS CLI di server (untuk cadangan ke S3)

```bash
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscli.zip
unzip -q /tmp/awscli.zip -d /tmp && sudo /tmp/aws/install
aws --version
aws sts get-caller-identity      # harus menampilkan warkah-ec2-role
```

Kredensial datang dari IAM role instance — tidak ada access key yang perlu disimpan.

### 5.3 Pengguna layanan

Aplikasi tidak boleh berjalan sebagai `root` maupun `ubuntu`:

```bash
sudo adduser --system --group --home /opt/warkah --shell /bin/bash warkah
```

### 5.4 Swap (wajib bila memakai `t3.small`)

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

### 5.5 Firewall lokal (lapis kedua)

Security Group sudah menjadi firewall utama. `ufw` dipasang sebagai jaring pengaman bila
suatu saat SG dilonggarkan:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
sudo ufw status
```

Port 8000 (Uvicorn) dan 8080 (Jenkins) tidak dibuka di mana pun — keduanya hanya
mendengarkan `127.0.0.1` dan dilayani lewat Nginx.

### 5.6 Pembaruan keamanan otomatis

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 6. Volume data & struktur direktori

### 6.1 Memformat dan memasang volume data

```bash
lsblk                          # volume 20 GB biasanya tampil sebagai nvme1n1
sudo mkfs.ext4 -L warkah-data /dev/nvme1n1     # HANYA untuk volume baru & kosong
sudo mkdir -p /var/lib/warkah
echo 'LABEL=warkah-data /var/lib/warkah ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount -a
df -h /var/lib/warkah
```

> `mkfs` menghapus isi volume. Pastikan `lsblk` menunjuk volume 20 GB yang baru dibuat,
> **bukan** volume root 30 GB. Pemasangan lewat `LABEL=` membuat konfigurasi tetap benar
> walaupun nama perangkat NVMe berubah urutan setelah *restart*.

### 6.2 Struktur direktori

```
/opt/warkah/                   (volume root)
├── releases/                  # satu folder per build Jenkins
│   ├── 12/
│   └── 13/
├── current -> releases/13     # symlink ke rilis aktif
├── venv/                      # virtualenv aplikasi
└── bin/{deploy.sh,cadangan.sh}

/var/lib/warkah/               (volume data terpisah — tidak tersentuh deploy)
├── warkah.db
├── rahasia.txt
└── cadangan/
```

```bash
sudo mkdir -p /opt/warkah/{releases,bin} /var/lib/warkah/cadangan
sudo chown -R warkah:warkah /opt/warkah /var/lib/warkah
sudo chmod 750 /var/lib/warkah
```

### 6.3 Membuat kunci sesi baru

```bash
sudo -u warkah bash -c 'python3 -c "import secrets; print(secrets.token_urlsafe(48))" > /var/lib/warkah/rahasia.txt'
sudo chmod 600 /var/lib/warkah/rahasia.txt
```

### 6.4 Memindahkan basis data yang sudah ada

Dari komputer Windows (PowerShell). **Hentikan dulu aplikasi di Windows** agar berkas WAL
tertutup rapi:

```powershell
scp -i $HOME\.ssh\warkah-key.pem D:\data_hak_milik\app_warkah\warkah.db ubuntu@<ELASTIC-IP>:/tmp/warkah.db
```

Di server:

```bash
sudo mv /tmp/warkah.db /var/lib/warkah/warkah.db
sudo chown warkah:warkah /var/lib/warkah/warkah.db
sudo chmod 640 /var/lib/warkah/warkah.db
sudo -u warkah sqlite3 /var/lib/warkah/warkah.db "PRAGMA integrity_check;"   # harus 'ok'
```

Bila memulai dari kosong, lewati langkah ini — migrasi akan membuat basis data baru.

---

## 7. Deploy manual pertama kali

Lakukan sekali secara manual agar yakin aplikasi jalan, baru setelah itu Jenkins
mengambil alih.

```bash
sudo -u warkah git clone https://github.com/<akun>/app-warkah.git /opt/warkah/releases/manual

sudo -u warkah python3 -m venv /opt/warkah/venv
sudo -u warkah /opt/warkah/venv/bin/pip install --upgrade pip
sudo -u warkah /opt/warkah/venv/bin/pip install -r /opt/warkah/releases/manual/requirements.txt

# sambungkan data bersama ke dalam rilis
sudo -u warkah ln -sfn /var/lib/warkah/warkah.db    /opt/warkah/releases/manual/warkah.db
sudo -u warkah ln -sfn /var/lib/warkah/rahasia.txt  /opt/warkah/releases/manual/rahasia.txt

# siapkan/naikkan skema (lihat bagian 2c)
cd /opt/warkah/releases/manual
sudo -u warkah /opt/warkah/venv/bin/python -c "import db; db.siapkan().close()"

# aktifkan rilis
sudo -u warkah ln -sfn /opt/warkah/releases/manual /opt/warkah/current

# uji cepat
sudo -u warkah /opt/warkah/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

Dari sesi SSH lain: `curl -I http://127.0.0.1:8000/masuk` harus menjawab `200`.
Hentikan dengan `Ctrl+C`, lanjut ke systemd.

### 7.1 Akun awal

Bila basis data baru, buat akun admin dan empat petugas:

```bash
cd /opt/warkah/current
sudo -u warkah /opt/warkah/venv/bin/python kelola_pengguna.py awal
```

Sandi acak yang tercetak **hanya muncul sekali** — catat, sampaikan ke orangnya, lalu
minta diganti lewat menu *Ganti sandi*.

---

## 8. Service systemd

Buat `/etc/systemd/system/warkah.service`:

```ini
[Unit]
Description=Aplikasi Warkah - Kantah Bone Bolango
After=network-online.target var-lib-warkah.mount
Wants=network-online.target
RequiresMountsFor=/var/lib/warkah

[Service]
Type=exec
User=warkah
Group=warkah
WorkingDirectory=/opt/warkah/current
Environment="PYTHONUNBUFFERED=1"
Environment="TZ=Asia/Makassar"

# letak basis data & folder salinan unggahan, di luar folder rilis (bagian 2a & 2d)
Environment="WARKAH_DB=/var/lib/warkah/warkah.db"
Environment="WARKAH_UNGGAHAN=/var/lib/warkah/unggahan"

# skema/migrasi dijalankan sebelum aplikasi naik (lihat bagian 2c)
ExecStartPre=/opt/warkah/venv/bin/python -c "import db; db.siapkan().close()"

ExecStart=/opt/warkah/venv/bin/uvicorn app:app \
    --host 127.0.0.1 --port 8000 \
    --workers 2 \
    --proxy-headers --forwarded-allow-ips="127.0.0.1" \
    --log-level info

Restart=always
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=20

# pengamanan
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/var/lib/warkah

[Install]
WantedBy=multi-user.target
```

> `RequiresMountsFor=/var/lib/warkah` penting di EC2: bila volume data gagal ter-*mount*
> setelah *restart*, aplikasi **tidak** dijalankan — lebih baik mati daripada diam-diam
> membuat basis data kosong baru di volume root.

> `WorkingDirectory` sengaja menunjuk ke symlink `current`, jadi setiap `restart`
> otomatis memakai rilis terbaru tanpa mengubah berkas unit.

Aktifkan:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now warkah
sudo systemctl status warkah --no-pager
journalctl -u warkah -n 50 --no-pager
```

Agar pengguna `warkah` (dipakai Jenkins) boleh me-*restart* servicenya sendiri tanpa
sandi, buat `/etc/sudoers.d/warkah`:

```
warkah ALL=(root) NOPASSWD: /bin/systemctl restart warkah, /bin/systemctl status warkah, /bin/systemctl is-active warkah
```

```bash
sudo visudo -cf /etc/sudoers.d/warkah      # verifikasi sintaks
sudo chmod 440 /etc/sudoers.d/warkah
sudo -u warkah sudo -n /bin/systemctl is-active warkah   # harus menjawab 'active'
```

---

## 9. Nginx, domain, dan HTTPS

### 9.1 Mengarahkan domain

Arahkan `A record` domain ke Elastic IP. Bila memakai Route 53:

```powershell
aws route53 change-resource-record-sets --hosted-zone-id <ZONE-ID> --change-batch '{
  \"Changes\": [{
    \"Action\": \"UPSERT\",
    \"ResourceRecordSet\": {
      \"Name\": \"warkah.contoh.go.id\",
      \"Type\": \"A\",
      \"TTL\": 300,
      \"ResourceRecords\": [{\"Value\": \"<ELASTIC-IP>\"}]
    }
  }]
}'
```

Verifikasi sebelum lanjut: `nslookup warkah.contoh.go.id` harus menjawab Elastic IP.

### 9.2 Konfigurasi Nginx

Buat `/etc/nginx/sites-available/warkah` — mulai dengan HTTP saja, biar Certbot yang
menambahkan blok TLS:

```nginx
upstream warkah_app {
    server 127.0.0.1:8000;
    keepalive 16;
}

server {
    listen 80;
    server_name warkah.contoh.go.id;

    # borang besar (penugasan & pemeriksaan massal)
    client_max_body_size 32m;

    access_log /var/log/nginx/warkah.access.log;
    error_log  /var/log/nginx/warkah.error.log;

    # berkas statis dilayani Nginx langsung
    location /static/ {
        alias /opt/warkah/current/static/;
        expires 7d;
        access_log off;
    }

    location / {
        proxy_pass http://warkah_app;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/warkah /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 9.3 Sertifikat Let's Encrypt

Karena server ada di internet dan port 80 terbuka, ini jalur termudah dan gratis:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d warkah.contoh.go.id --redirect --agree-tos -m admin@contoh.go.id
sudo systemctl status certbot.timer      # perpanjangan otomatis
sudo certbot renew --dry-run
```

Certbot menulis ulang berkas Nginx di atas: menambah blok `listen 443 ssl`, sertifikat,
dan pengalihan dari port 80.

> **Tanpa domain?** Let's Encrypt tidak melayani alamat IP. Pilihannya: pakai sertifikat
> mandiri (peramban akan memberi peringatan sampai `warkah.crt` dipasang di komputer
> pegawai), atau taruh instance di belakang **Application Load Balancer** dengan
> sertifikat **AWS Certificate Manager** — ACM gratis, tetapi ALB menambah biaya bulanan
> yang lebih besar daripada instance-nya sendiri.

### 9.4 Cookie sesi di belakang HTTPS (dianjurkan)

`SessionMiddleware` di `app.py:700` belum menyetel `https_only`. Setelah HTTPS aktif,
tambahkan supaya cookie sesi tidak pernah terkirim lewat HTTP polos:

```python
Middleware(SessionMiddleware,
           secret_key=auth.kunci_rahasia(os.path.join(BASE_DIR, "rahasia.txt")),
           session_cookie="warkah_sesi", max_age=60 * 60 * 12, same_site="lax",
           https_only=True),
```

Perubahan ini masuk repo dan ikut ter-deploy lewat pipeline seperti perubahan lain.
Jangan diaktifkan sebelum langkah 9.3 selesai — pengguna tidak akan bisa masuk.

---

## 10. Memasang Jenkins

```bash
sudo apt install -y fontconfig openjdk-21-jre

sudo wget -O /usr/share/keyrings/jenkins-keyring.asc \
  https://pkg.jenkins.io/debian-stable/jenkins.io-2023.key

echo "deb [signed-by=/usr/share/keyrings/jenkins-keyring.asc] \
  https://pkg.jenkins.io/debian-stable binary/" \
  | sudo tee /etc/apt/sources.list.d/jenkins.list > /dev/null

sudo apt update
sudo apt install -y jenkins python3-venv rsync
sudo systemctl enable --now jenkins
sudo systemctl status jenkins --no-pager
```

### 10.1 Jenkins di belakang Nginx (wajib di panduan ini)

Karena port 8080 tidak dibuka di Security Group, Jenkins diakses lewat
`https://warkah.contoh.go.id/jenkins/`.

Setel prefix Jenkins:

```bash
sudo systemctl edit jenkins
```

```ini
[Service]
Environment="JENKINS_PREFIX=/jenkins"
```

```bash
sudo systemctl restart jenkins
```

Tambahkan ke blok `server` HTTPS (blok yang dibuat Certbot) di
`/etc/nginx/sites-available/warkah`:

```nginx
    location /jenkins/ {
        proxy_pass http://127.0.0.1:8080/jenkins/;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
        proxy_request_buffering off;
        client_max_body_size 64m;
    }
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 10.2 Wisaya awal

Buka `https://warkah.contoh.go.id/jenkins/`, tempel sandi awal:

```bash
sudo cat /var/lib/jenkins/secrets/initialAdminPassword
```

Pilih **Install suggested plugins**, buat akun admin dengan sandi kuat, lalu tambahkan
plugin lewat *Manage Jenkins → Plugins → Available*:

- **Git** dan **GitHub** (biasanya sudah ikut)
- **Pipeline: Stage View** — tampilan tahapan
- **SSH Agent** — deploy lewat SSH
- **Blue Ocean** *(opsional)*

Terakhir, isi *Jenkins URL* di *Manage Jenkins → System* dengan
`https://warkah.contoh.go.id/jenkins/`. Nilai ini dipakai untuk membentuk URL webhook.

> **Jangan** biarkan Jenkins tanpa autentikasi. Ia terbuka ke internet lewat 443, dan
> Jenkins tanpa autentikasi berarti eksekusi perintah jarak jauh oleh siapa pun.
> Pastikan *Manage Jenkins → Security* memakai *Jenkins' own user database* dengan
> *Logged-in users can do anything*, dan **matikan** *Allow users to sign up*.

---

## 11. Kredensial & akses Jenkins ke server

Jenkins men-deploy lewat SSH ke pengguna `warkah` — walaupun satu mesin. Cara ini membuat
pipeline tetap sama persis bila nanti aplikasi dipindah ke instance terpisah.

### 11.1 Kunci SSH untuk Jenkins

```bash
sudo -u jenkins ssh-keygen -t ed25519 -N "" -C "jenkins-deploy" -f /var/lib/jenkins/.ssh/id_ed25519

sudo mkdir -p /opt/warkah/.ssh
sudo cat /var/lib/jenkins/.ssh/id_ed25519.pub | sudo tee -a /opt/warkah/.ssh/authorized_keys
sudo chown -R warkah:warkah /opt/warkah/.ssh
sudo chmod 700 /opt/warkah/.ssh
sudo chmod 600 /opt/warkah/.ssh/authorized_keys

# percayai host sekali agar tidak ada prompt saat pipeline berjalan
sudo -u jenkins ssh-keyscan -H 127.0.0.1 | sudo -u jenkins tee -a /var/lib/jenkins/.ssh/known_hosts
sudo -u jenkins ssh -o BatchMode=yes warkah@127.0.0.1 'echo koneksi-ok'
```

### 11.2 Mendaftarkan kredensial di Jenkins

*Manage Jenkins → Credentials → System → Global credentials → Add Credentials*

| Kredensial | Jenis | ID | Isi |
|---|---|---|---|
| Kunci deploy | *SSH Username with private key* | `warkah-ssh` | Username `warkah`, tempel isi kunci privat `/var/lib/jenkins/.ssh/id_ed25519` |
| Akses GitHub | *Username with password* | `github-warkah` | Username GitHub + **Personal Access Token** (bukan sandi akun) |

PAT GitHub dibuat di *Settings → Developer settings → Personal access tokens →
Fine-grained*, dengan izin **Contents: Read-only** dan **Webhooks: Read & write**, hanya
pada repositori ini.

> Kunci `.pem` EC2 **tidak** dimasukkan ke Jenkins. Jenkins hanya perlu menjangkau
> `warkah@127.0.0.1`, bukan `ubuntu@`.

---

## 12. Script deploy di server

`/opt/warkah/bin/deploy.sh` dipanggil Jenkins lewat SSH. Inilah yang benar-benar
mengaktifkan rilis baru.

```bash
#!/usr/bin/env bash
# Aktifkan rilis baru Aplikasi Warkah.
# Pemakaian: deploy.sh <nomor-rilis>
set -euo pipefail

RILIS="${1:?nomor rilis wajib diisi}"
DASAR=/opt/warkah
DATA=/var/lib/warkah
BARU="$DASAR/releases/$RILIS"
VENV="$DASAR/venv"
SIMPAN_RILIS=5

[[ -d "$BARU" ]] || { echo "Rilis $BARU tidak ada"; exit 1; }

# pastikan volume data EBS benar-benar ter-mount sebelum menyentuh apa pun
mountpoint -q "$DATA" || { echo "GAGAL: $DATA bukan mount point (volume EBS lepas?)"; exit 1; }

# rilis yang sedang aktif, untuk rollback otomatis bila gagal
LAMA=""
[[ -L "$DASAR/current" ]] && LAMA="$(readlink -f "$DASAR/current")"

echo "==> Dependensi"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -r "$BARU/requirements.txt" --quiet

echo "==> Menyambungkan data bersama"
ln -sfn "$DATA/warkah.db"   "$BARU/warkah.db"
ln -sfn "$DATA/rahasia.txt" "$BARU/rahasia.txt"

echo "==> Cadangan basis data sebelum migrasi"
mkdir -p "$DATA/cadangan"
if [[ -s "$DATA/warkah.db" ]]; then
    sqlite3 "$DATA/warkah.db" ".backup '$DATA/cadangan/pra-$RILIS-$(date +%Y%m%d%H%M).db'"
fi

echo "==> Migrasi skema"
cd "$BARU"
"$VENV/bin/python" -c "import db; db.siapkan().close()"

echo "==> Mengaktifkan rilis $RILIS"
ln -sfn "$BARU" "$DASAR/current"
sudo /bin/systemctl restart warkah

echo "==> Uji kesehatan"
sehat=0
kode=""
for i in $(seq 1 15); do
    kode="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/masuk || true)"
    if [[ "$kode" == "200" ]]; then sehat=1; break; fi
    sleep 2
done

if [[ "$sehat" -ne 1 ]]; then
    echo "!! Aplikasi tidak sehat (HTTP $kode). Mengembalikan rilis sebelumnya."
    if [[ -n "$LAMA" ]]; then
        ln -sfn "$LAMA" "$DASAR/current"
        sudo /bin/systemctl restart warkah
    fi
    exit 1
fi

echo "==> Membersihkan rilis lama (menyisakan $SIMPAN_RILIS)"
cd "$DASAR/releases"
ls -1dt */ | tail -n +$((SIMPAN_RILIS + 1)) | xargs -r rm -rf

echo "==> Deploy rilis $RILIS berhasil"
```

Pasang:

```bash
sudo -u warkah nano /opt/warkah/bin/deploy.sh    # tempel isi di atas
sudo chown warkah:warkah /opt/warkah/bin/deploy.sh
sudo chmod 750 /opt/warkah/bin/deploy.sh
bash -n /opt/warkah/bin/deploy.sh                # periksa sintaks
```

> Bisa juga dipanggil manual: `sudo -u warkah /opt/warkah/bin/deploy.sh 12`

---

## 13. Jenkinsfile (pipeline CI/CD)

Simpan sebagai `Jenkinsfile` **di akar repositori**, lalu *commit* dan *push*.

```groovy
pipeline {
    agent any

    environment {
        // Jenkins satu instance dengan aplikasi. Bila Jenkins dipindah ke
        // instance lain, ganti menjadi 'warkah@<IP-PRIVAT-EC2-APLIKASI>'
        // (pakai IP privat VPC, bukan Elastic IP), dan izinkan port 22
        // dari Security Group Jenkins di SG aplikasi.
        APP_HOST   = 'warkah@127.0.0.1'
        RELEASES   = '/opt/warkah/releases'
        DEPLOY_SH  = '/opt/warkah/bin/deploy.sh'
        SSH_CRED   = 'warkah-ssh'
        HEALTH_URL = 'http://127.0.0.1:8000/masuk'
    }

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 20, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                sh 'git log -1 --pretty="%h %an %s"'
            }
        }

        stage('Siapkan lingkungan uji') {
            steps {
                sh '''
                    python3 -m venv .venv
                    .venv/bin/pip install --upgrade pip --quiet
                    .venv/bin/pip install -r requirements.txt --quiet
                '''
            }
        }

        stage('Uji') {
            parallel {
                stage('Sintaks Python') {
                    steps {
                        sh '.venv/bin/python -m compileall -q app.py db.py auth.py laporan.py kelola_pengguna.py import_data.py'
                    }
                }
                stage('Sintaks template') {
                    steps {
                        sh '''
                            .venv/bin/python - <<'PY'
from jinja2 import Environment, FileSystemLoader
import pathlib, sys
env = Environment(loader=FileSystemLoader("templates"))
gagal = 0
for berkas in sorted(pathlib.Path("templates").glob("*.html")):
    try:
        env.parse(berkas.read_text(encoding="utf-8"), filename=str(berkas))
        print("ok   ", berkas)
    except Exception as e:
        print("GAGAL", berkas, e)
        gagal += 1
sys.exit(1 if gagal else 0)
PY
                        '''
                    }
                }
                stage('Cek kebocoran data') {
                    steps {
                        sh '''
                            if git ls-files | grep -E "^(warkah\\.db|rahasia\\.txt)$|\\.xlsx$|\\.pem$"; then
                                echo "BERHENTI: berkas data/rahasia/kunci ikut di repositori"
                                exit 1
                            fi
                            echo "Tidak ada berkas data/rahasia di repositori."
                        '''
                    }
                }
            }
        }

        stage('Uji asap') {
            steps {
                sh '''
                    # jalankan aplikasi sungguhan atas basis data sementara —
                    # tidak menyentuh data produksi sama sekali
                    TMP="$(mktemp -d)"
                    cp -r templates static app.py db.py auth.py "$TMP"/
                    cd "$TMP"
                    "$WORKSPACE/.venv/bin/python" -c "import db; db.siapkan().close()"
                    "$WORKSPACE/.venv/bin/uvicorn" app:app --host 127.0.0.1 --port 8765 &
                    UVI=$!
                    kode=""
                    for i in $(seq 1 20); do
                        kode="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/masuk || true)"
                        [ "$kode" = "200" ] && break
                        sleep 1
                    done
                    kill $UVI 2>/dev/null || true
                    cd "$WORKSPACE"
                    rm -rf "$TMP"
                    [ "$kode" = "200" ] || { echo "Uji asap gagal (HTTP $kode)"; exit 1; }
                    echo "Uji asap lulus."
                '''
            }
        }

        stage('Paket') {
            steps {
                sh '''
                    rm -rf paket && mkdir paket
                    tar --exclude-vcs \
                        --exclude=".venv" --exclude="paket" --exclude="__pycache__" \
                        --exclude="warkah.db" --exclude="rahasia.txt" \
                        -czf paket/warkah-${BUILD_NUMBER}.tar.gz .
                    ls -lh paket/
                '''
                archiveArtifacts artifacts: 'paket/*.tar.gz', fingerprint: true
            }
        }

        stage('Deploy') {
            when { branch 'main' }
            steps {
                sshagent(credentials: [env.SSH_CRED]) {
                    sh '''
                        set -e
                        ssh -o StrictHostKeyChecking=accept-new $APP_HOST \
                            "mkdir -p $RELEASES/${BUILD_NUMBER}"
                        scp paket/warkah-${BUILD_NUMBER}.tar.gz \
                            $APP_HOST:$RELEASES/${BUILD_NUMBER}/
                        ssh $APP_HOST "cd $RELEASES/${BUILD_NUMBER} \
                            && tar xzf warkah-${BUILD_NUMBER}.tar.gz \
                            && rm warkah-${BUILD_NUMBER}.tar.gz"
                        ssh $APP_HOST "$DEPLOY_SH ${BUILD_NUMBER}"
                    '''
                }
            }
        }

        stage('Verifikasi') {
            when { branch 'main' }
            steps {
                sshagent(credentials: [env.SSH_CRED]) {
                    sh '''
                        ssh $APP_HOST "sudo /bin/systemctl is-active warkah"
                        ssh $APP_HOST "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' $HEALTH_URL"
                        ssh $APP_HOST "readlink -f /opt/warkah/current"
                    '''
                }
            }
        }
    }

    post {
        success { echo "Build ${env.BUILD_NUMBER} sukses pada cabang ${env.BRANCH_NAME}." }
        failure { echo "Build ${env.BUILD_NUMBER} GAGAL. Periksa log tahap yang merah." }
        always  { sh 'rm -rf .venv paket || true' }
    }
}
```

Alur singkatnya:

| Tahap | Yang dikerjakan | Berhenti bila |
|---|---|---|
| Checkout | Ambil kode dari GitHub | — |
| Siapkan | Buat venv & pasang dependensi | Dependensi gagal dipasang |
| Uji | Sintaks Python, sintaks template, cek berkas data/rahasia/kunci | Salah satu gagal |
| Uji asap | Menjalankan aplikasi atas basis data sementara | `/masuk` bukan `200` |
| Paket | `tar.gz` tanpa data & rahasia, diarsipkan Jenkins | — |
| Deploy | Kirim + jalankan `deploy.sh` (hanya cabang `main`) | `deploy.sh` gagal → rollback otomatis |
| Verifikasi | Service aktif & HTTP 200 | Tidak sehat |

---

## 14. Membuat job di Jenkins

Gunakan **Multibranch Pipeline** agar `main` dan `dev` tertangani otomatis:

1. *New Item* → nama `warkah` → **Multibranch Pipeline** → OK
2. *Branch Sources* → **GitHub**
   - Credentials: `github-warkah`
   - Repository HTTPS URL: `https://github.com/<akun>/app-warkah`
3. *Behaviours* → **Discover branches**: `Exclude branches that are also filed as PRs`
4. *Build Configuration* → *by Jenkinsfile* → Script Path: `Jenkinsfile`
5. *Scan Repository Triggers* → centang **Periodically if not otherwise run**: `1 hour`
   (jaring pengaman bila webhook gagal)
6. **Save** → Jenkins memindai repo dan langsung menjalankan build untuk tiap cabang

Cabang `dev` berhenti setelah tahap *Paket* karena `when { branch 'main' }` — persis yang
diinginkan: diuji, tidak di-deploy.

---

## 15. Webhook GitHub

Server sekarang punya Elastic IP dan HTTPS publik, jadi webhook bisa dipakai — inilah
keuntungan terbesar pindah ke AWS dibanding server di dalam kantor.

**Di GitHub** — repo → *Settings → Webhooks → Add webhook*:

| Kolom | Isi |
|---|---|
| Payload URL | `https://warkah.contoh.go.id/jenkins/github-webhook/` (perhatikan garis miring di akhir) |
| Content type | `application/json` |
| SSL verification | *Enable* (bisa, karena sertifikatnya Let's Encrypt yang sah) |
| Secret | *(opsional; bila diisi, daftarkan juga di Jenkins)* |
| Events | *Just the push event* |

**Di Jenkins**: *Manage Jenkins → System → GitHub → Add GitHub Server*, pakai kredensial
`github-warkah`, lalu **Test connection**.

Uji: ubah satu baris di `PANDUAN.md`, `git push`, build harus muncul dalam beberapa detik.
Di GitHub, *Webhooks → Recent Deliveries* harus menunjukkan `200`.

> **Bila port 443 dibatasi hanya ke IP kantor** (bagian 3.3), webhook GitHub akan
> tertolak. Izinkan rentang IP webhook GitHub di Security Group:
>
> ```bash
> curl -s https://api.github.com/meta | python3 -c "import sys,json; print('\n'.join(json.load(sys.stdin)['hooks']))"
> ```
>
> Rentang ini sesekali berubah, jadi banyak yang memilih mengandalkan *Scan Repository
> Triggers* periodik saja (langkah 14.5) — jeda maksimal 1 jam, turunkan ke `5 minutes`
> bila perlu lebih gesit.

---

## 16. Rollback

### 16.1 Rollback aplikasi

`deploy.sh` sudah mengembalikan rilis sebelumnya secara otomatis bila uji kesehatan
gagal. Untuk rollback manual:

```bash
ls -1dt /opt/warkah/releases/*/          # daftar rilis, terbaru di atas
sudo -u warkah ln -sfn /opt/warkah/releases/12 /opt/warkah/current
sudo systemctl restart warkah
curl -I https://warkah.contoh.go.id/masuk
```

### 16.2 Rollback basis data

Dari cadangan pra-migrasi yang dibuat `deploy.sh`:

```bash
sudo systemctl stop warkah
sudo -u warkah cp /var/lib/warkah/cadangan/pra-13-202608271430.db /var/lib/warkah/warkah.db
sudo systemctl start warkah
```

Dari S3 (bagian 17):

```bash
aws s3 ls s3://warkah-cadangan-<akun>/harian/
aws s3 cp s3://warkah-cadangan-<akun>/harian/warkah-20260826-1900.db.gz /tmp/
sudo systemctl stop warkah
gunzip -c /tmp/warkah-20260826-1900.db.gz | sudo -u warkah tee /var/lib/warkah/warkah.db > /dev/null
sudo systemctl start warkah
```

### 16.3 Pemulihan dari EBS snapshot

Bila volume data rusak total:

```powershell
# buat volume baru dari snapshot, di AZ yang sama dengan instance
$VOLBARU = aws ec2 create-volume --availability-zone ap-southeast-3a `
  --snapshot-id snap-xxxxxxxx --volume-type gp3 --query "VolumeId" --output text

aws ec2 detach-volume --volume-id <VOL-LAMA>
aws ec2 attach-volume --volume-id $VOLBARU --instance-id $IID --device /dev/sdf
```

Di server: `sudo mount -a && sudo systemctl restart warkah`. Karena `/etc/fstab` memakai
`LABEL=warkah-data`, volume hasil snapshot langsung dikenali tanpa perubahan konfigurasi.

---

## 17. Cadangan: S3 + EBS snapshot

Tiga lapis: cadangan pra-migrasi otomatis (`deploy.sh`), cadangan harian ke S3, dan
snapshot EBS.

### 17.1 Bucket S3 di Jakarta

```powershell
$AKUN = aws sts get-caller-identity --query Account --output text
$BUCKET = "warkah-cadangan-$AKUN"

aws s3api create-bucket --bucket $BUCKET --region ap-southeast-3 `
  --create-bucket-configuration LocationConstraint=ap-southeast-3

aws s3api put-public-access-block --bucket $BUCKET `
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws s3api put-bucket-encryption --bucket $BUCKET `
  --server-side-encryption-configuration '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"AES256\"}}]}'

aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled
```

Aturan daur hidup — pindah ke penyimpanan murah setelah 30 hari, hapus setelah setahun.
`daurhidup.json`:

```json
{
  "Rules": [{
    "ID": "cadangan-warkah",
    "Status": "Enabled",
    "Filter": { "Prefix": "harian/" },
    "Transitions": [{ "Days": 30, "StorageClass": "STANDARD_IA" }],
    "Expiration": { "Days": 365 }
  }]
}
```

```powershell
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration file://daurhidup.json
```

### 17.2 Izin IAM untuk instance

`kebijakan-s3.json` (ganti `<BUCKET>`) — hanya tulis, tanpa hak hapus:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::<BUCKET>",
        "arn:aws:s3:::<BUCKET>/*"
      ]
    }
  ]
}
```

```powershell
aws iam put-role-policy --role-name warkah-ec2-role `
  --policy-name warkah-s3-cadangan --policy-document file://kebijakan-s3.json
```

### 17.3 Script cadangan harian

`/opt/warkah/bin/cadangan.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
DATA=/var/lib/warkah
TUJUAN=$DATA/cadangan
BUCKET="s3://warkah-cadangan-<AKUN>"
mkdir -p "$TUJUAN"
CAP="$TUJUAN/warkah-$(date +%Y%m%d-%H%M).db"

# .backup aman dijalankan saat aplikasi sedang berjalan (mode WAL)
sqlite3 "$DATA/warkah.db" ".backup '$CAP'"
gzip -f "$CAP"

# kirim ke S3 Jakarta
aws s3 cp "$CAP.gz" "$BUCKET/harian/" --only-show-errors
echo "Terkirim: $BUCKET/harian/$(basename "$CAP").gz"

# simpan 7 hari terakhir di disk (sisanya sudah aman di S3)
find "$TUJUAN" -name "warkah-*.db.gz" -mtime +7 -delete
find "$TUJUAN" -name "pra-*.db"       -mtime +14 -delete
```

```bash
sudo chown warkah:warkah /opt/warkah/bin/cadangan.sh
sudo chmod 750 /opt/warkah/bin/cadangan.sh
sudo -u warkah /opt/warkah/bin/cadangan.sh      # uji sekarang
aws s3 ls s3://warkah-cadangan-<AKUN>/harian/
```

`/etc/systemd/system/warkah-cadangan.service`:

```ini
[Unit]
Description=Cadangan basis data Warkah ke S3

[Service]
Type=oneshot
User=warkah
ExecStart=/opt/warkah/bin/cadangan.sh
```

`/etc/systemd/system/warkah-cadangan.timer`:

```ini
[Unit]
Description=Cadangan Warkah setiap hari pukul 19:00 WITA

[Timer]
OnCalendar=*-*-* 19:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now warkah-cadangan.timer
systemctl list-timers warkah-cadangan --no-pager
```

### 17.4 Snapshot EBS otomatis

Data Lifecycle Manager membuat snapshot harian volume yang bertanda `Name=warkah-data`.
Lewat konsol: **EC2 → Lifecycle Manager → Create lifecycle policy → EBS snapshot
policy**, target tag `Name=warkah-data`, jadwal harian 20:00, simpan 14 snapshot.

Snapshot manual sebelum pekerjaan berisiko (impor data, upgrade Ubuntu):

```powershell
aws ec2 create-snapshot --volume-id <VOL-DATA> `
  --description "Warkah sebelum impor KKP $(Get-Date -Format yyyy-MM-dd)" `
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Name,Value=warkah-data-manual}]'
```

> Cadangan tidak pernah teruji sampai pernah dipulihkan. Sekali dalam tiga bulan,
> pulihkan cadangan S3 terbaru ke instance uji dan pastikan aplikasi bisa dibuka.

### 17.5 Rotasi log

Log aplikasi masuk ke journald dan dirotasi otomatis. Batasi ukurannya di
`/etc/systemd/journald.conf` agar volume root 30 GB tidak penuh:

```ini
SystemMaxUse=500M
```

Log Nginx — `/etc/logrotate.d/warkah`:

```
/var/log/nginx/warkah.*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 www-data adm
    sharedscripts
    postrotate
        systemctl reload nginx > /dev/null 2>&1 || true
    endscript
}
```

---

## 18. Impor data KKP di server

`import_data.py` butuh pandas + openpyxl yang sengaja tidak dipasang di venv produksi.
Pakai venv terpisah:

```bash
sudo -u warkah python3 -m venv /opt/warkah/venv-data
sudo -u warkah /opt/warkah/venv-data/bin/pip install \
    -r /opt/warkah/current/requirements-data.txt
```

Menjalankan impor:

```bash
# kirim berkas xlsx dari komputer kerja
scp -i $HOME\.ssh\warkah-key.pem Gabungan_Hak_Milik_18XX.xlsx ubuntu@<ELASTIC-IP>:/tmp/

# import_data.py mencari sumber di direktori INDUK folder aplikasi:
#   DATA_DIR = dirname(dirname(__file__))  ->  /opt/warkah/releases
sudo mv /tmp/Gabungan_Hak_Milik_*.xlsx /opt/warkah/releases/
sudo chown warkah:warkah /opt/warkah/releases/Gabungan_Hak_Milik_*.xlsx

# selalu berhenti, snapshot, dan cadangkan dulu
sudo systemctl stop warkah
sudo -u warkah /opt/warkah/bin/cadangan.sh
cd /opt/warkah/current
sudo -u warkah /opt/warkah/venv-data/bin/python import_data.py
sudo systemctl start warkah
```

> Impor menulis ulang tabel `bidang` dan `wilayah`, sedangkan tabel kerja (penyimpanan,
> pemeriksaan, peminjaman, penugasan) tidak disentuh. Berkas xlsx diletakkan di
> `/opt/warkah/releases/` — di luar folder rilis, jadi tidak ikut terhapus saat
> pembersihan rilis lama.

> Pandas pada `t3.small` bisa kehabisan memori untuk berkas xlsx besar. Bila terjadi,
> pastikan swap aktif (bagian 5.4), atau naikkan sementara tipe instance:
> `aws ec2 stop-instances` → `modify-instance-attribute --instance-type t3.large` →
> `start-instances`. Elastic IP tetap menempel setelah *restart*.

---

## 19. Pengamanan AWS

### 19.1 Yang sudah tertangani panduan ini

| Hal | Penanganan |
|---|---|
| Port terbuka | Hanya 22 (IP kantor), 80, 443. Uvicorn & Jenkins hanya `127.0.0.1` |
| Kredensial di server | Tidak ada access key — memakai IAM role instance |
| Pencurian kredensial via SSRF | IMDSv2 diwajibkan (`HttpTokens=required`) |
| Data saat disimpan | Volume root & data EBS terenkripsi; bucket S3 terenkripsi |
| Data saat dikirim | HTTPS Let's Encrypt, pengalihan otomatis dari port 80 |
| Sandi pengguna | PBKDF2-SHA256 200.000 iterasi (`auth.py`) — sudah memadai |
| Cadangan terhapus | Versioning S3 aktif; kebijakan IAM tanpa hak `s3:DeleteObject` |

### 19.2 Tambahan yang dianjurkan

```bash
# fail2ban untuk SSH dan Nginx
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

Matikan SSH berbasis sandi (seharusnya sudah mati di AMI Ubuntu, pastikan saja) —
`/etc/ssh/sshd_config.d/99-warkah.conf`:

```
PasswordAuthentication no
PermitRootLogin no
```

```bash
sudo systemctl reload ssh
```

### 19.3 Session Manager sebagai cadangan akses

IAM role sudah memuat `AmazonSSMManagedInstanceCore`, jadi bila `.pem` hilang atau IP
kantor berubah, server tetap bisa dimasuki tanpa SSH:

```powershell
aws ssm start-session --target i-xxxxxxxxxxxx --region ap-southeast-3
```

Dengan ini, port 22 bahkan boleh ditutup sama sekali di Security Group.

### 19.4 Catatan tata kelola data

Ini data pertanahan milik instansi. Sebelum produksi, pastikan:

- Repositori GitHub **privat**, akses hanya untuk yang berkepentingan
- Bucket S3 tidak pernah dibuat publik (sudah dikunci di 17.1)
- Region `ap-southeast-3` (Jakarta) dipilih sadar agar data tetap di wilayah Indonesia —
  jangan menyalin cadangan ke region atau layanan lain tanpa persetujuan
- Akses konsol AWS memakai MFA, dan tidak memakai user root untuk operasi harian

---

## 20. Perkiraan biaya

Perkiraan kasar per bulan untuk region `ap-southeast-3`, harga *on-demand*, di luar PPN:

| Komponen | Perkiraan |
|---|---|
| EC2 `t3.medium` (730 jam) | ± USD 38–45 |
| EBS `gp3` 50 GB (30 root + 20 data) | ± USD 5 |
| Elastic IP (IPv4 publik) | ± USD 4 |
| Snapshot EBS (14 harian, inkremental) | ± USD 1–2 |
| S3 cadangan (< 10 GB) | < USD 1 |
| Transfer keluar (< 100 GB/bulan) | Gratis |
| **Total** | **± USD 50–58** |

> Angka ini perkiraan, bukan kutipan resmi. Verifikasi di
> <https://calculator.aws/#/> dengan region *Asia Pacific (Jakarta)*.

Cara menghemat:

- **`t3.small` + swap**: memangkas biaya EC2 sekitar setengah. Cukup untuk aplikasi
  saja; agak sesak bila Jenkins ikut di dalamnya.
- **Savings Plan / Reserved Instance 1 tahun**: hemat sekitar 30–40 % bila server memang
  akan berjalan terus.
- **Instance dimatikan di luar jam kerja**: bila aplikasi hanya dipakai jam kantor,
  jadwalkan `stop`/`start` lewat EventBridge Scheduler. Elastic IP tetap menempel, tapi
  tetap ditagih saat instance mati.

Pasang alarm biaya agar tidak kaget:

```powershell
aws budgets create-budget --account-id $AKUN --budget '{
  \"BudgetName\": \"warkah-bulanan\",
  \"BudgetLimit\": {\"Amount\": \"70\", \"Unit\": \"USD\"},
  \"TimeUnit\": \"MONTHLY\",
  \"BudgetType\": \"COST\"
}'
```

---

## 21. Pemeliharaan & pemecahan masalah

### Perintah harian

```bash
sudo systemctl status warkah              # kondisi layanan
journalctl -u warkah -f                   # log langsung
journalctl -u warkah --since "1 hour ago" # log satu jam terakhir
readlink -f /opt/warkah/current           # rilis yang sedang aktif
df -h /var/lib/warkah /                   # sisa ruang kedua volume
mountpoint /var/lib/warkah                # volume data masih ter-mount?
sudo systemctl restart warkah             # muat ulang
```

### Masalah yang sering muncul

| Gejala | Sebab & penanganan |
|---|---|
| `502 Bad Gateway` di Nginx | Service mati. Lihat `journalctl -u warkah -n 80`; sering karena dependensi gagal dipasang atau `warkah.db` tidak bisa ditulis |
| `no such table: petugas` | Migrasi belum jalan. `cd /opt/warkah/current && sudo -u warkah /opt/warkah/venv/bin/python -c "import db; db.siapkan().close()"` (bagian 2c) |
| Setelah *restart* instance, data seperti kosong | Volume EBS data tidak ter-*mount*. `mountpoint /var/lib/warkah`, lalu `sudo mount -a`. Service sengaja menolak jalan dalam kondisi ini (`RequiresMountsFor`) |
| `attempt to write a readonly database` | Kepemilikan salah: `sudo chown warkah:warkah /var/lib/warkah/warkah.db*` — perhatikan berkas `-wal` dan `-shm` juga |
| `database is locked` berulang | Turunkan ke `--workers 1` di `warkah.service` |
| Semua pengguna terlempar keluar setelah deploy | `rahasia.txt` berganti. Pastikan symlink ke `/var/lib/warkah/rahasia.txt` masih ada |
| Data kembali ke kondisi lama setelah deploy | `warkah.db` ikut ter-deploy dari repo. Periksa `.gitignore`, lalu pastikan `WARKAH_DB` menunjuk `/var/lib/warkah/warkah.db` — bandingkan dengan keluaran `python -c "import db; print(db.DB_PATH)"` |
| Tampilan CSS tidak berubah | `versi_gaya()` membaca *mtime* `static/style.css`; setelah deploy *mtime*-nya baru, cukup muat ulang paksa (`Ctrl+F5`) |
| Situs tidak bisa dibuka dari kantor | Cek Security Group (port 443 & IP kantor), lalu `sudo systemctl status nginx`. Bila IP publik kantor berubah, perbarui aturan SG |
| Certbot gagal perpanjang | Port 80 harus tetap terbuka `0.0.0.0/0`. Uji: `sudo certbot renew --dry-run` |
| Jenkins gagal: `Permission denied (publickey)` | Kunci publik Jenkins belum ada di `/opt/warkah/.ssh/authorized_keys`, atau izin folder salah (harus 700/600) |
| Jenkins gagal: `sudo: a terminal is required` | Baris `/etc/sudoers.d/warkah` belum ada atau salah. Uji: `sudo -u warkah sudo -n /bin/systemctl is-active warkah` |
| Jenkins lambat / build mati sendiri | Memori habis. `free -h`; aktifkan swap (5.4) atau naikkan tipe instance |
| Webhook tidak memicu build | *Recent Deliveries* di GitHub bukan `200`; periksa *Jenkins URL* di *Manage Jenkins → System* dan garis miring akhir pada `/jenkins/github-webhook/` |
| Volume root penuh | Rilis lama menumpuk (`deploy.sh` menyisakan 5) atau journald. `sudo journalctl --vacuum-size=200M` |

### Pembaruan sistem

```bash
sudo -u warkah /opt/warkah/bin/cadangan.sh     # cadangkan dulu
sudo apt update && sudo apt upgrade -y
sudo systemctl restart warkah nginx jenkins
```

Lakukan di luar jam kerja. Untuk pembaruan besar (upgrade rilis Ubuntu), buat snapshot
EBS kedua volume lebih dulu.

### Memperbesar volume tanpa mematikan aplikasi

```powershell
aws ec2 modify-volume --volume-id <VOL-DATA> --size 40
```

```bash
sudo resize2fs /dev/nvme1n1
df -h /var/lib/warkah
```

---

## 22. Daftar periksa

**Infrastruktur AWS**

- [ ] Semua sumber daya berada di region `ap-southeast-3` (Jakarta)
- [ ] Security Group: 22 hanya dari IP kantor; 8080 **tidak** terbuka
- [ ] Elastic IP terpasang dan tercatat
- [ ] IMDSv2 diwajibkan (`HttpTokens=required`)
- [ ] Volume root & data terenkripsi; `lsblk` menunjukkan volume data ter-*mount* di `/var/lib/warkah`
- [ ] IAM role terpasang; `aws sts get-caller-identity` di server menampilkan `warkah-ec2-role`
- [ ] Alarm biaya (AWS Budgets) aktif

**Repositori & pipeline**

- [ ] `.gitignore`, `requirements.txt`, `requirements-data.txt`, `Jenkinsfile` sudah ada di repo
- [ ] `git ls-files` **tidak** memuat `warkah.db`, `rahasia.txt`, `*.xlsx`, atau `*.pem`
- [ ] Repositori GitHub berstatus **privat**
- [ ] Jenkins memakai autentikasi; pendaftaran mandiri dimatikan
- [ ] `git push` ke `dev` → build jalan, **tidak** men-deploy
- [ ] `git push` ke `main` → build jalan, deploy, verifikasi hijau
- [ ] Webhook GitHub menunjukkan `200` di *Recent Deliveries*

**Aplikasi**

- [ ] `/var/lib/warkah/warkah.db` dan `rahasia.txt` ada, milik `warkah`, mode 640/600
- [ ] `sudo systemctl show warkah -p Environment` memuat `WARKAH_DB=/var/lib/warkah/warkah.db`
      (atau, bila masih memakai cara lama, `ls -l /opt/warkah/current/warkah.db` menunjukkan
      **symlink** ke `/var/lib/warkah/`)
- [ ] `sudo systemctl is-enabled warkah` → `enabled`
- [ ] `curl -I https://warkah.contoh.go.id/masuk` → `200`
- [ ] `sudo certbot renew --dry-run` berhasil
- [ ] Masuk dengan akun admin berhasil; menu Katalog, Sirkulasi, dan Monitoring terbuka
- [ ] `date` di server menunjukkan waktu WITA yang benar

**Cadangan & pemulihan**

- [ ] `systemctl list-timers warkah-cadangan` menunjukkan jadwal berikutnya
- [ ] `aws s3 ls s3://warkah-cadangan-<akun>/harian/` menampilkan berkas terbaru
- [ ] Kebijakan snapshot DLM aktif untuk volume `warkah-data`
- [ ] Rollback rilis sudah dicoba sekali dan berhasil
- [ ] Pemulihan cadangan dari S3 sudah dicoba sekali dan berhasil
