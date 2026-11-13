"""
Tahap 6 - Menjalankan seluruh eksperimen Bab 4 berurutan: greedy, beberapa konfigurasi GA, lalu perbandingan.

Konfigurasi GA (tiap konfigurasi = 30 run independen, seed 5000-5029, data pembersihan yang sama):
  bab3                  Tabel 7 Bab 3 persis (mutasi 10% gen). Konfigurasi dasar penelitian.
  mutasi1               Sama dengan bab3 kecuali mutasi 1% gen. Dasar: pada uji awal mutasi 10% merusak populasi (biaya rata-rata
                        populasi naik ratusan kali lipat; lihat analisis_ga). Laju mutasi yang kecil lazim pada penjadwalan GA
                        (mis. Mahlous & Mahlous 2023 memakai laju jauh di bawah 1%). Ini PENYIMPANGAN dari Tabel 7 yang harus dijelaskan.
  mutasi1_tanpa_repair  mutasi1 tanpa repair: menunjukkan sumbangan repair (ablasi).
  mutasi1_awal_acak     (opsional, --ablasi-awal) mutasi1 dengan populasi awal acak: menunjukkan ketergantungan GA pada greedy.

Contoh:
  python src/eksperimen.py --ulangan 3 --generasi 300      # uji cepat seluruh alur (menit)
  python src/eksperimen.py --proses 4                      # eksperimen penuh, 4 run paralel (jam)
  python src/eksperimen.py --cek                           # hanya cetak perintah yang akan dijalankan

Setiap langkah menulis folder sendiri (data/greedy/greedyN, data/ga/gaN, data/perbandingan/perbandinganN), tidak menimpa.
Waktu: satu run GA penuh (3000 generasi) bisa belasan menit; 30 run x 3 konfigurasi sekitar belasan jam tanpa paralel.
"""
import argparse
import subprocess
import sys
from pathlib import Path

AWAL = Path(__file__).resolve().parent


def cari_src():
    """Folder yang berisi greedy.py/ga.py/bandingkan.py: folder berkas ini, atau subfolder src bila berkas ini salah letak."""
    for c in (AWAL, AWAL / "src", AWAL.parent / "src"):
        if all((c / f).exists() for f in ("greedy.py", "ga.py", "bandingkan.py", "pemodelan.py", "laporan.py")):
            return c
    sys.exit("greedy.py, ga.py, bandingkan.py, pemodelan.py, dan laporan.py tidak ditemukan.\n"
             f"Letakkan semuanya di folder yang sama (src\\). Berkas ini berada di: {AWAL}")


SRC = cari_src()


LOG = None


def cetak(teks, akhir="\n"):
    print(teks, end=akhir, flush=True)
    if LOG:
        LOG.write(teks + akhir)
        LOG.flush()


def jalankan(perintah, cek):
    cetak("\n$ " + " ".join(perintah))
    if cek:
        return None
    hasil = None
    with subprocess.Popen(perintah, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(SRC), bufsize=1, env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}) as p:
        for ln in p.stdout:
            cetak(ln, "")
            if ln.startswith("Hasil di "):
                hasil = ln[len("Hasil di "):].strip()
        p.wait()
        if p.returncode not in (0, 1):    # kode 1 = jadwal tidak sah, bukan galat
            sys.exit(f"Langkah gagal (kode {p.returncode}): {' '.join(perintah)}")
    return hasil


def main():
    ap = argparse.ArgumentParser(description="Jalankan eksperimen greedy + GA + perbandingan")
    ap.add_argument("--hasil", help="folder pembersihan hasilN (bawaan: terbaru)")
    ap.add_argument("--ulangan", type=int, default=30)
    ap.add_argument("--seed", type=int, default=5000)
    ap.add_argument("--generasi", type=int, default=3000)
    ap.add_argument("--proses", type=int, default=1, help="run GA paralel")
    ap.add_argument("--ablasi-awal", action="store_true", help="tambah konfigurasi populasi awal acak")
    ap.add_argument("--cek", action="store_true", help="hanya cetak perintah")
    a = ap.parse_args()
    global LOG
    if not a.cek:
        gdir = SRC.parent / "data"
        gdir.mkdir(parents=True, exist_ok=True)
        nomor = 1
        while (gdir / f"log_eksperimen{nomor}.txt").exists():
            nomor += 1
        LOG = open(gdir / f"log_eksperimen{nomor}.txt", "w", encoding="utf-8")
        cetak(f"Log lengkap disimpan di {gdir / f'log_eksperimen{nomor}.txt'}")
    py = sys.executable   # PYTHONUNBUFFERED=1 diset di jalankan() agar log langsung tampil
    dasar = ["--hasil", a.hasil] if a.hasil else []

    folder_greedy = jalankan([py, str(SRC / "greedy.py"), "--percobaan", str(a.ulangan), "--seed", str(a.seed)] + dasar, a.cek)
    umum = ["--ulangan", str(a.ulangan), "--seed", str(a.seed), "--generasi", str(a.generasi), "--proses", str(a.proses), "--log", "100"] + dasar
    konfigurasi = [("bab3", []), ("mutasi1", ["--persen-mutasi", "1"]),
                   ("mutasi1_tanpa_repair", ["--persen-mutasi", "1", "--tanpa-repair"])]
    if a.ablasi_awal:
        konfigurasi.append(("mutasi1_awal_acak", ["--persen-mutasi", "1", "--awal", "acak"]))
    folder_ga = []
    for label, ekstra in konfigurasi:
        f = jalankan([py, str(SRC / "ga.py"), "--label", label] + ekstra + umum, a.cek)
        folder_ga.append(f)
    if a.cek:
        print("\n$ python bandingkan.py --greedy <greedyN> --ga <gaN> ...")
        return 0
    if not folder_greedy or not all(folder_ga):
        sys.exit("Folder keluaran tidak terbaca dari log; jalankan bandingkan.py manual dengan --greedy dan --ga.")
    jalankan([py, str(SRC / "bandingkan.py"), "--greedy", folder_greedy, "--ga"] + folder_ga, False)
    return 0


if __name__ == "__main__":
    sys.exit(main())