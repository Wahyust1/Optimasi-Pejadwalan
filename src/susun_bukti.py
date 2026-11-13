"""
susun_bukti.py - Merapikan folder penelitian TANPA menghapus apa pun.

Yang dikerjakan (urutan):
  1. Memilah setiap folder di data\\ menjadi DIPAKAI (angka/gambar laporan), PENDUKUNG (dirujuk berkas lain) atau ARSIP
     (percobaan lama yang tidak dipakai laporan). Aturan ada di bagian KONFIGURASI di bawah dan dapat diubah.
  2. Memindahkan folder ARSIP ke data\\_arsip\\ (bukan dihapus) dan mencatatnya di data\\_arsip\\LOG_PINDAH.csv.
  3. Menyalin berkas bukti yang dipakai laporan ke bukti_laporan\\<bab>\\ dengan nama berawalan nomor tabel/gambar,
     lalu menulis bukti_laporan\\manifest.csv (nama di laporan, sumber, md5, ukuran, tanggal) agar tiap angka dapat ditelusuri.
  4. Menulis peta_folder.csv (fungsi tiap folder), peta_berkas.csv (fungsi tiap berkas di folder yang dipakai),
     peta_src.csv (fungsi tiap skrip) dan PETA.md (ringkasan yang mudah dibaca) ke folder bukti_laporan.

Keselamatan: tanpa --jalankan skrip hanya MENAMPILKAN rencana (simulasi). Dengan --jalankan skrip meminta ketikan "ya".
Skrip tidak pernah menghapus. Folder yang tidak dikenal dibiarkan dan dilaporkan. Pemindahan dapat dibatalkan dengan --kembalikan.

Contoh (jalankan dari folder Penjadwalan_JTE):
  python src\\susun_bukti.py                 # simulasi
  python src\\susun_bukti.py --jalankan      # kerjakan setelah konfirmasi
  python src\\susun_bukti.py --kembalikan    # kembalikan folder arsip ke tempat asal
"""
import argparse
import ast
import csv
import hashlib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ============================ KONFIGURASI ============================
# Folder yang DIPAKAI laporan: (path relatif terhadap data\\, fungsi, dipakai di)
DIPAKAI = {
    "raw": ("Data mentah asli (kelas, dosen, BKD, ruangan, kurikulum). Tidak diubah.", "Bab IV 4.1"),
    "referensi": ("Berkas acuan penelitian (aturan, keputusan).", "Bab III, 4.1"),
    "bersih/hasil5": ("Data bersih hasil persiapan_data.py; masukan semua algoritma (212 kelas).", "Bab IV 4.1-4.2; semua hasil"),
    "greedy/greedy5": ("Greedy heuristik 30 percobaan (25 sah); sumber skala acuan f_ref dan perbandingan4.", "Bab IV 4.4, 4.6; skala f_ref"),
    "greedy/greedy8": ("Greedy heuristik 5000 percobaan.", "Tabel 14"),
    "greedy/greedy9": ("Greedy heuristik 10000 percobaan.", "Tabel 14"),
    "greedy/greedy10": ("Greedy kriteria penalti 30 percobaan (0 sah); bukti kelas gagal.", "Gambar 4"),
    "ga/ga13": ("GA mutasi 1% dengan repair; sumber jadwal final (seed 5008).", "Tabel 15; Bab IV 4.5-4.8"),
    "ga/ga15": ("GA mutasi 1% tanpa repair (pembanding).", "Tabel 15"),
    "ga/ga16": ("GA mutasi 10% dengan repair (konfigurasi Bab III, pembanding).", "Tabel 15"),
    "perbandingan/perbandingan4": ("Statistik greedy vs GA dan 5 grafik (skala greedy5).", "Tabel 16; Gambar 5-9"),
    "perbandingan_jadwal": ("Papan skor 15 aspek antar jadwal.", "Tabel 16"),
    "jadwal/kandidat/kandidat3": ("Katalog dan rekomendasi kandidat (aturan K1-K5).", "Tabel 18; Gambar 12"),
    "jadwal/final5": ("Jadwal final yang diserahkan beserta laporan, audit, dan catatan asal.", "Tabel 19; Gambar 10"),
    "pemeriksaan": ("Bukti pemeriksaan: audit independen, bukti visual, cek jadwal, uji revisi.", "Bab IV 4.7, 4.9"),
    "rekap": ("Rekap angka satu skala dari rekap_angka.py beserta jejak.", "Seluruh angka Bab IV"),
}
# Folder yang tidak dipakai langsung tetapi dirujuk berkas lain: jangan diarsipkan
PENDUKUNG = {
    "greedy/greedy1": ("Greedy heuristik 30 percobaan format lama; dirujuk katalog kandidat3.", "Tabel 18 (baris greedy-30)"),
    "jadwal/kandidat/kandidat1": ("Katalog kandidat awal; asal_jadwal.txt final5 menunjuk ke sini.", "Catatan asal final5"),
}
# Induk yang berisi folder bernomor; semua yang bukan DIPAKAI/PENDUKUNG di dalamnya diarsipkan
INDUK_BERNOMOR = {
    "ga": r"ga\d+", "greedy": r"greedy\d+", "perbandingan": r"perbandingan\d+", "bersih": r"hasil\d+",
    "jadwal": r"(jadwal|final)\d+", "jadwal/kandidat": r"kandidat\d+",
}
# Berkas bukti untuk laporan: (bab, label di laporan, folder sumber, pola berkas)
BUKTI = [
    ("bab4_4.1", "Tabel9_temuan_data", "bersih/hasil5", ["laporan_temuan5.csv", "laporan_kelayakan_awal5.csv", "status5.txt"]),
    ("bab4_4.4", "Gambar3_analisis_greedy5", "greedy/greedy5", ["analisis_greedy5.txt", "ringkasan_percobaan5.csv"]),
    ("bab4_4.4", "Gambar4_kelas_gagal_greedy10", "greedy/greedy10", ["kelas_gagal10.csv", "analisis_greedy10.txt"]),
    ("bab4_4.4", "Tabel14_greedy_5000", "greedy/greedy8", ["analisis_greedy8.txt"]),
    ("bab4_4.4", "Tabel14_greedy_10000", "greedy/greedy9", ["analisis_greedy9.txt"]),
    ("bab4_4.5", "Tabel15_ga13_mutasi1", "ga/ga13", ["statistik_ulangan13.txt", "ringkasan_ulangan13.csv", "parameter13.csv", "analisis_ga13.txt"]),
    ("bab4_4.5", "Tabel15_ga15_tanpa_repair", "ga/ga15", ["ringkasan_ulangan15.csv", "parameter15.csv"]),
    ("bab4_4.5", "Tabel15_ga16_mutasi10", "ga/ga16", ["ringkasan_ulangan16.csv", "parameter16.csv"]),
    ("bab4_4.5", "Gambar5-7_grafik_ga", "perbandingan/perbandingan4", ["grafik_konvergensi4.png", "grafik_suku_lunak4.png", "grafik_waktu_iterasi4.png"]),
    ("bab4_4.6", "Gambar8-9_grafik_statistik", "perbandingan/perbandingan4", ["grafik_boxplot_fref4.png", "grafik_uji_statistik4.png"]),
    ("bab4_4.6", "Tabel16_statistik", "perbandingan/perbandingan4", ["perbandingan4.txt", "ringkasan_metode4.csv", "uji_statistik4.csv"]),
    ("bab4_4.6", "Tabel16_papan_skor", "perbandingan_jadwal", ["**/papan_skor*.xlsx", "**/kesimpulan*.txt"]),
    ("bab4_4.7", "Gambar10_audit_final5", "jadwal/final5/audit_hard", ["audit.txt", "pelanggaran.csv"]),
    ("bab4_4.7", "Gambar11_bukti_visual", "pemeriksaan/bukti_visual1", ["*.xlsx", "*.png"]),
    ("bab4_4.8", "Tabel18_Gambar12_katalog", "jadwal/kandidat/kandidat3", ["rekomendasi.txt", "katalog_kandidat.xlsx", "katalog_kandidat.csv"]),
    ("bab4_4.8", "Tabel19_jadwal_final", "jadwal/final5", ["jadwal_final.csv", "asal_jadwal.txt", "laporan_validasi.csv"]),
    ("bab4_4.9", "Tabel20_cek_jadwal", "pemeriksaan", ["cek*/hasil_cek.txt"]),
    ("bab4_4.9", "Gambar13_sesi_revisi", "jadwal/final5/revisi1", ["ringkasan.txt", "riwayat_perubahan.csv"]),
    ("bab4_4.9", "Tabel21_Gambar14_uji_revisi", "pemeriksaan", ["uji_revisi*/uji_revisi.txt"]),
    ("rekap", "rekap_angka_satu_skala", "rekap", ["rekap*/*.csv", "rekap*/jejak.txt"]),
]
# Fungsi berkas menurut pola nama (dipakai untuk peta_berkas.csv)
FUNGSI_PATH = [   # aturan menurut lokasi (dicek lebih dulu): (pola path, fungsi)
    (r"/jadwal_per_run/(awal|akhir)_seed", "Jadwal satu ulangan GA (populasi awal atau hasil akhir) menurut seed; bahan uji berpasangan"),
    (r"/jadwal_per_percobaan/", "Jadwal satu percobaan greedy menurut seed"),
    (r"/laporan/jadwal_[A-Za-z]+\.csv$", "Jadwal per program studi"),
    (r"/laporan/(dosen_|hari_|blok_kosong|grid_)", "Laporan turunan jadwal: beban dosen, hari, blok kosong, grid paket/ruangan"),
    (r"/laporan/analisis_jadwal", "Analisis tertulis jadwal (pelanggaran dan keseimbangan)"),
    (r"/laporan/", "Laporan turunan jadwal (per prodi, dosen, hari, ruangan, paket)"),
    (r"^data/raw/", "Data mentah asli dari sumber (BKD, kelas, dosen, ruangan, kurikulum); tidak diubah"),
    (r"^data/referensi/", "Berkas acuan penelitian"),
    (r"^data/perbandingan_jadwal/", "Perbandingan jadwal 15 aspek: papan skor, perubahan per kelas, kesimpulan, grafik"),
    (r"^data/jadwal/kandidat/kandidat\d+/[^/]+/", "Folder satu kandidat: jadwal terpilih dan laporan turunannya"),
]
FUNGSI_BERKAS = [
    (r"^jadwal_(ga|greedy|final)?\d*\.csv$|^jadwal\.csv$|^jadwal_final\.csv$", "Jadwal terbaik (satu baris per kelas: hari, jam, ruangan, dosen)"),
    (r"^laporan_validasi", "Hitungan pelanggaran hard constraint H1-H6 dan nilai soft S1-S6 oleh validator"),
    (r"^rincian_pelanggaran", "Daftar pelanggaran hard constraint per kelas"),
    (r"^kelas_gagal", "Kelas yang gagal dijadwalkan beserta ukuran domain"),
    (r"^ringkasan_percobaan", "Hasil tiap percobaan greedy (seed, kelas gagal, S1-S6, waktu)"),
    (r"^ringkasan_ulangan", "Hasil tiap ulangan GA (seed, S1-S6, biaya awal/akhir, generasi, waktu)"),
    (r"^ringkasan_sementara", "Ringkasan sementara saat GA berjalan (cadangan)"),
    (r"^analisis_", "Analisis tertulis satu percobaan (sumber tangkapan layar)"),
    (r"^statistik_ulangan", "Statistik 30 ulangan GA"),
    (r"^parameter", "Parameter GA yang dipakai (jejak konfigurasi)"),
    (r"^riwayat_biaya", "Biaya terbaik tiap generasi (grafik konvergensi)"),
    (r"^status", "Status proses (metode, sumber data, sah atau tidak)"),
    (r"^perbandingan\d*\.txt$", "Ringkasan perbandingan greedy dan GA dan cara membacanya"),
    (r"^ringkasan_metode", "Statistik ringkas per metode (f_ref)"),
    (r"^uji_statistik", "Hasil Wilcoxon, Mann-Whitney, Cliff's delta"),
    (r"^grafik_", "Grafik untuk laporan"),
    (r"^papan_skor", "Papan skor 15 aspek antar jadwal"),
    (r"^katalog_kandidat", "Katalog kandidat jadwal dengan skor dan peringkat"),
    (r"^rekomendasi", "Peringkat kandidat dan rekomendasi (aturan K1-K5)"),
    (r"^asal_jadwal", "Catatan asal jadwal final dan hasil validasi"),
    (r"^audit\.txt$", "Hasil audit independen H1-H6"),
    (r"^pelanggaran\.csv$", "Daftar pelanggaran temuan audit"),
    (r"^dosen_audit", "Rekap audit per dosen"),
    (r"^bukti_visual", "Peta hunian Excel dengan rumus hidup (bukti visual)"),
    (r"^heatmap_", "Heatmap hunian dosen, paket, atau ruangan"),
    (r"^hasil_cek", "Hasil pemeriksaan jadwal oleh cek_jadwal.py"),
    (r"^ringkasan\.txt$", "Ringkasan satu sesi revisi atau uji"),
    (r"^riwayat_perubahan", "Riwayat tiap perubahan pada sesi revisi"),
    (r"^jadwal_baru", "Jadwal hasil revisi (jadwal asli tidak diubah)"),
    (r"^uji_revisi", "Hasil uji U1-U6 alat revisi"),
    (r"^skala_acuan|^angka_|^jejak", "Rekap angka satu skala dan jejak proses (rekap_angka.py)"),
    (r"^kelas\d*\.csv$|^dosen|^ruangan|^blok_waktu|^kelompok_paket|^mata_kuliah|^aturan_ruangan|^kelas_dosen", "Tabel data bersih untuk model"),
    (r"^laporan_temuan|^laporan_kelayakan|^keputusan|^tinjauan|^bkd_tanpa|^kelas_dikecualikan", "Catatan temuan dan keputusan pembersihan data"),
]
# =====================================================================


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tulis(p, kepala, baris):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(kepala)
        w.writerows(baris)


def fungsi_berkas(nama, rel=""):
    rel = rel.replace("\\", "/")
    for pola, ket in FUNGSI_PATH:
        if re.search(pola, rel):
            return ket
    for pola, ket in FUNGSI_BERKAS:
        if re.search(pola, nama, re.I):
            return ket
    return "(belum dijelaskan: tambahkan pola di FUNGSI_BERKAS)"


def rencana_arsip(data):
    """Kembalikan (arsip, dikenal_dipakai, tak_dikenal)."""
    keep = {k.replace("\\", "/") for k in list(DIPAKAI) + list(PENDUKUNG)}
    arsip, tak = [], []
    for induk, pola in INDUK_BERNOMOR.items():
        d = data / induk
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            rel = f"{induk}/{sub.name}"
            if rel in keep or sub.name.startswith("_"):
                continue
            if induk == "jadwal" and sub.name == "kandidat":
                continue
            if re.fullmatch(pola, sub.name):
                arsip.append(sub)
            else:
                tak.append(sub)
    return arsip, tak


def kumpulkan_bukti(data):
    baris, hilang = [], []
    for bab, label, folder, pola in BUKTI:
        base = data / folder
        for pl in pola:
            cocok = sorted(base.glob(pl)) if base.exists() else []
            cocok = [c for c in cocok if c.is_file()]
            if not cocok:
                hilang.append((bab, label, f"{folder}/{pl}"))
            baris += [(bab, label, c) for c in cocok]
    return baris, hilang


def ringkas_skrip(src):
    out = []
    for p in sorted(src.glob("*.py")):
        try:
            doc = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")), clean=True) or ""
        except Exception:
            doc = ""
        baris = [x.strip() for x in doc.splitlines() if x.strip()]
        out.append((p.name, baris[0] if baris else "(tanpa docstring)"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="folder Penjadwalan_JTE (berisi data\\ dan src\\)")
    ap.add_argument("--jalankan", action="store_true")
    ap.add_argument("--kembalikan", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    data, src = root / "data", root / "src"
    if not data.is_dir():
        sys.exit(f"Tidak ada folder data di {root}. Jalankan dari folder Penjadwalan_JTE atau isi --root.")
    log = data / "_arsip" / "LOG_PINDAH.csv"

    if a.kembalikan:
        if not log.exists():
            sys.exit("Tidak ada LOG_PINDAH.csv; tidak ada yang dikembalikan.")
        with open(log, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        print(f"{len(rows)} folder akan dikembalikan ke tempat asal.")
        if a.jalankan or input('Ketik "ya" untuk melanjutkan: ').strip().lower() == "ya":
            for r in rows:
                tj, asal = Path(r["tujuan"]), Path(r["asal"])
                if tj.exists() and not asal.exists():
                    asal.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tj), str(asal))
                    print("  kembali:", asal)
                else:
                    print("  dilewati (sumber tidak ada atau tujuan sudah ada):", asal)
        return

    arsip, tak = rencana_arsip(data)
    bukti, hilang = kumpulkan_bukti(data)
    print(f"Folder penelitian: {root}\n")
    print(f"DIPAKAI ({len(DIPAKAI)}), PENDUKUNG ({len(PENDUKUNG)}), ARSIP ({len(arsip)}).")
    print("Akan DIPINDAH ke data\\_arsip (tidak dihapus):")
    for s in arsip:
        print("  ", s.relative_to(data))
    if tak:
        print("Folder tidak dikenal (dibiarkan, periksa sendiri):")
        for s in tak:
            print("  ", s.relative_to(data))
    print(f"Akan DISALIN ke bukti_laporan: {len(bukti)} berkas dari {len({b[1] for b in bukti})} kelompok.")
    if hilang:
        print("Berkas bukti yang BELUM ADA (akan dicatat MISSING di manifest):")
        for h in hilang:
            print("  ", h[1], "->", h[2])
    if not a.jalankan:
        print("\nSIMULASI selesai; belum ada yang diubah. Jalankan lagi dengan --jalankan.")
        return
    if input('\nKetik "ya" untuk melaksanakan: ').strip().lower() != "ya":
        print("Dibatalkan.")
        return

    # 1. arsip
    cat = []
    for s in arsip:
        tujuan = data / "_arsip" / s.relative_to(data)
        tujuan.parent.mkdir(parents=True, exist_ok=True)
        if tujuan.exists():
            print("  dilewati (sudah ada di arsip):", s.relative_to(data))
            continue
        shutil.move(str(s), str(tujuan))
        cat.append([str(s), str(tujuan), datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    if cat:
        lama = []
        if log.exists():
            with open(log, encoding="utf-8-sig", newline="") as f:
                lama = [[r["asal"], r["tujuan"], r["waktu"]] for r in csv.DictReader(f)]
        tulis(log, ["asal", "tujuan", "waktu"], lama + cat)

    # 2. bukti
    keluar = root / "bukti_laporan"
    man = []
    for bab, label, c in bukti:
        tj = keluar / bab / f"{label}__{c.name}"
        tj.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(c, tj)
        man.append([label, bab, str(tj.relative_to(root)), str(c.relative_to(root)), md5(c), c.stat().st_size,
                    datetime.fromtimestamp(c.stat().st_mtime).strftime("%Y-%m-%d %H:%M"), "ADA"])
    for bab, label, pola in hilang:
        man.append([label, bab, "-", pola, "-", "-", "-", "MISSING"])
    tulis(keluar / "manifest.csv", ["nama_di_laporan", "bab", "berkas_bukti", "sumber", "md5", "ukuran_byte", "tanggal_ubah", "status"], man)

    # 3. peta
    pf = []
    for k, (fg, di) in {**{k: v for k, v in DIPAKAI.items()}, **PENDUKUNG}.items():
        st = "DIPAKAI" if k in DIPAKAI else "PENDUKUNG"
        pf.append([f"data/{k}", st, fg, di])
    for s in arsip:
        pf.append([f"data/_arsip/{s.relative_to(data)}", "ARSIP", "Percobaan lama yang tidak dipakai laporan; disimpan sebagai jejak.", "-"])
    tulis(keluar / "peta_folder.csv", ["folder", "status", "fungsi", "dipakai_di_laporan"], pf)

    pb = []
    for k in list(DIPAKAI) + list(PENDUKUNG):
        d = data / k
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    pb.append([str(p.relative_to(root)), fungsi_berkas(p.name, str(p.relative_to(root))), p.stat().st_size])
    tulis(keluar / "peta_berkas.csv", ["berkas", "fungsi", "ukuran_byte"], pb)
    sk = ringkas_skrip(src) if src.is_dir() else []
    tulis(keluar / "peta_src.csv", ["skrip", "fungsi"], sk)

    md = ["# Peta Folder Penelitian", f"Dibuat: {datetime.now():%Y-%m-%d %H:%M}", "",
          "Status: DIPAKAI = angka atau gambar laporan; PENDUKUNG = dirujuk berkas lain; ARSIP = percobaan lama (data\\_arsip).", "",
          "| Folder | Status | Fungsi | Dipakai di |", "|---|---|---|---|"]
    md += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for r in pf]
    md += ["", "## Skrip di src", "", "| Skrip | Fungsi |", "|---|---|"] + [f"| {n} | {f} |" for n, f in sk]
    md += ["", "Bukti per bab ada di bukti_laporan\\<bab>; daftar lengkap dan md5 di manifest.csv; fungsi tiap berkas di peta_berkas.csv."]
    (keluar / "PETA.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nSelesai. {len(cat)} folder diarsipkan, {len(bukti)} berkas disalin, {len(hilang)} berkas bukti belum ada.")
    print(f"Lihat {keluar}\\PETA.md dan manifest.csv. Pembatalan: python src\\susun_bukti.py --kembalikan")


if __name__ == "__main__":
    main()