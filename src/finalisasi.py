"""
Finalisasi: kumpulkan jadwal yang dipilih beserta semua bukti dan laporannya dalam SATU folder untuk Kajur.

Folder eksperimen (data\\greedy\\greedyN, data\\ga\\gaN) berisi semua run dan hanya untuk penelitian. Folder hasil akhir untuk Kajur
dibuat di sini: data\\jadwal\\finalN\\ . Skrip ini MENYALIN (tidak memindahkan) dan tidak menimpa folder final yang sudah ada.

Isi finalN:
  jadwal_final.csv         jadwal yang dipilih
  laporan\\                 jadwal per prodi, dosen, hari, ruangan, paket, grid, dan laporan_jadwal.xlsx (dari laporan.py)
  audit\\              bukti independen H1-H6 (audit.py)
  bukti_visualN\\           workbook peta hunian + heatmap (bukti_visual.py)
  asal_jadwal.txt          dari run mana jadwal ini, parameter, mengapa dipilih, hasil validasi, dan peringatan

Pemilihan: secara bawaan jadwal_gaN.csv, yaitu run dengan skor bersama terendah di antara run yang sah (dipilih ga.py). Bisa diganti
dengan --seed untuk memilih run tertentu, atau --berkas untuk jadwal apa pun (mis. hasil edit dari ubah_jadwal.py).
Skrip berhenti bila jadwal TIDAK sah: jadwal yang melanggar hard constraint tidak boleh menjadi jadwal final.

Contoh:
  python src\\finalisasi.py                              # gaN terbaru
  python src\\finalisasi.py --ga data\\ga\\ga2 --seed 5007
  python src\\finalisasi.py --berkas data\\ga\\ga2\\ubah1\\jadwal_baru.csv --catatan "geser Kalkulus 2 B atas permintaan Kajur"
"""
import argparse
import csv
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

from audit import tentukan
from laporan import buat_laporan
from pemodelan import BOBOT, Masalah, baca, baca_jadwal, nomor_berikut, tulis


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Kumpulkan jadwal final untuk Kajur")
    ap.add_argument("--ga", help="folder gaN sumber (bawaan: terbaru)")
    ap.add_argument("--seed", type=int, help="pakai hasil akhir run dengan seed ini (bawaan: run terbaik pilihan ga.py)")
    ap.add_argument("--berkas", help="pakai berkas jadwal ini (mis. hasil ubah_jadwal.py) sebagai ganti jadwal GA")
    ap.add_argument("--hasil", help="folder data bersih (bawaan: dari status)")
    ap.add_argument("--keluar", default=str(base / "jadwal"))
    ap.add_argument("--catatan", default="", help="catatan bebas yang ditulis ke asal_jadwal.txt")
    a = ap.parse_args()

    sumber_csv, ga_folder = None, None
    if a.berkas:
        sumber_csv = Path(a.berkas)
        ga_folder = Path(a.ga) if a.ga else sumber_csv.resolve().parent
    else:
        if a.ga:
            ga_folder = Path(a.ga)
        else:
            c = [(int(re.fullmatch(r"ga(\d+)", p.name).group(1)), p) for p in (base / "ga").glob("ga[0-9]*")
                 if re.fullmatch(r"ga\d+", p.name) and list(p.glob("status*.txt"))]
            if not c:
                sys.exit("Tidak ada folder gaN. Isi --ga atau --berkas.")
            ga_folder = max(c)[1]
        n = re.search(r"ga(\d+)$", ga_folder.name).group(1)
        sumber_csv = ga_folder / "jadwal_per_run" / f"akhir_seed{a.seed}.csv" if a.seed is not None else ga_folder / f"jadwal_ga{n}.csv"
    if not sumber_csv.exists():
        sys.exit(f"Berkas jadwal tidak ditemukan: {sumber_csv}")
    # status sumber (untuk folder data bersih) dicari di folder jadwal atau folder ga
    cek = next((p for p in (sumber_csv.resolve().parent, sumber_csv.resolve().parent.parent, ga_folder.resolve())
                if list(p.glob("status*.txt"))), ga_folder)
    _, hasil = tentukan(str(sumber_csv), a.hasil) if a.hasil else (None, None)
    if hasil is None:
        sumber = None
        for st in cek.glob("status*.txt"):
            for ln in st.read_text(encoding="utf-8").splitlines():
                if ln.startswith("SUMBER:"):
                    sumber = ln.split(":", 1)[1].strip()
        if not sumber:
            sys.exit("Folder data bersih tidak diketahui. Isi --hasil, mis. --hasil data\\bersih\\hasil5")
        hasil = str(base / "bersih" / sumber)
    ms = Masalah(Path(hasil))
    jd = baca_jadwal(sumber_csv)
    v, lunak, rinci = ms.validasi(jd)
    pel = sum(v.values())
    if pel or len(jd) != len(ms.kelas):
        sys.exit(f"BERHENTI: jadwal tidak sah (pelanggaran keras {pel}, terjadwal {len(jd)}/{len(ms.kelas)}). Jadwal final harus sah.")

    keluar = Path(a.keluar)
    keluar.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(keluar, "final")
    out = keluar / f"final{N}"
    out.mkdir()
    shutil.copy2(sumber_csv, out / "jadwal_final.csv")
    buat_laporan(ms, jd, out / "laporan")
    tulis(out / "laporan_validasi.csv", ["indikator", "nilai"], list(v.items()) + list(lunak.items()))

    # bukti: panggil skrip masing-masing pada jadwal final (hasilnya ditulis di folder final)
    src = Path(__file__).resolve().parent
    status_bukti = {}
    (out / "asal_jadwal.txt").write_text("(sedang dibuat)", encoding="utf-8")  # penanda: keluaran bukti ditaruh di folder final
    for nama, skrip in (("audit", "audit.py"), ("bukti_visual", "bukti_visual.py")):
        p = subprocess.run([sys.executable, str(src / skrip), "--jadwal", str(out / "jadwal_final.csv"), "--hasil", hasil],
                           capture_output=True, text=True)
        status_bukti[nama] = p.returncode
        if p.returncode not in (0,):
            print(f"PERINGATAN: {nama} selesai dengan kode {p.returncode}:\n{p.stdout[-600:]}{p.stderr[-600:]}")

    # asal jadwal
    L = [f"JADWAL FINAL {N}", f"Dibuat: {datetime.datetime.now():%Y-%m-%d %H:%M}", f"Data masukan: {Path(hasil).name} ({len(ms.kelas)} kelas)",
         f"Berkas sumber: {sumber_csv}"]
    if a.catatan:
        L.append(f"Catatan: {a.catatan}")
    L += ["", "HASIL VALIDASI (validator pemodelan.py dan audit independen audit.py)",
          f"  Pelanggaran keras H1-H6: {dict(v)}  -> SAH",
          f"  Suku lunak: {dict((k, x) for k, x in lunak.items())}",
          "  Audit independen: " + ("lolos (0 pelanggaran)" if status_bukti.get("audit") == 0 else "LIHAT audit" + chr(92) + "audit.txt")]
    if not a.berkas and ga_folder:
        n = re.search(r"ga(\d+)$", ga_folder.name).group(1)
        par = {}
        pf = ga_folder / f"parameter{n}.csv"
        if pf.exists():
            par = {r["parameter"]: r["nilai"] for r in baca(pf)}
        L += ["", "ASAL: ALGORITMA GENETIKA", f"  Folder: {ga_folder}", f"  Konfigurasi: {par.get('label', '-')}",
              f"  Populasi {par.get('populasi', '?')}, generasi maks {par.get('generasi_maks', '?')}, mutasi {par.get('persen_mutasi', '?')}% gen, "
              f"elitisme {par.get('elitisme', '?')}, repair {par.get('repair', '?')}"]
        rf = ga_folder / f"ringkasan_ulangan{n}.csv"
        if rf.exists():
            rs = baca(rf)
            if rs and "skor_bersama" in rs[0]:
                sb = sorted(float(r["skor_bersama"]) for r in rs if int(float(r["pelanggaran_keras"])) == 0)
                sd = a.seed if a.seed is not None else None
                if sd is None:
                    pil = min((r for r in rs if int(float(r["pelanggaran_keras"])) == 0), key=lambda r: float(r["skor_bersama"]))
                    sd = int(pil["seed"])
                else:
                    pil = next((r for r in rs if int(r["seed"]) == sd), None)
                if pil:
                    awalv = float(pil["skor_bersama_awal"])
                    L += [f"  Seed run terpilih: {sd}",
                          f"  Skor bersama (kecil = baik): akhir {float(pil['skor_bersama']):.3f}, populasi awal run yang sama {awalv:.3f}, "
                          f"median {len(sb)} run sah {sb[len(sb) // 2]:.3f}, terbaik {sb[0]:.3f}, terburuk {sb[-1]:.3f}",
                          f"  Generasi dijalankan: {pil['generasi']}, waktu run: {pil['detik']} detik"]
        L += ["", "MENGAPA JADWAL INI DIPILIH",
              "  Dipilih run dengan skor bersama (gabungan terbobot S1-S6 pada skala yang sama untuk semua run) terendah di antara run yang sah.",
              "  Aturan ini ditetapkan sebelum melihat hasil dan dapat diulang dengan seed yang sama. Perbandingan lengkap dengan greedy ada pada",
              "  data\\perbandingan_jadwal\\ (papan_skor.xlsx dan kesimpulan.txt)."]
    L += ["", "PERINGATAN",
          f"  - Bobot S1-S6 {BOBOT} adalah titik awal, belum dikalibrasi Kajur. Bila prioritas berbeda, ubah BOBOT di pemodelan.py dan jalankan ulang.",
          "  - Bukti bentrok dosen hanya sebaik data pengampu (BKD). Cocokkan dengan SK pengampu sebelum jadwal diumumkan.",
          "  - Jadwal dapat digeser manual dan diperiksa ulang dengan ubah_jadwal.py tanpa menjalankan GA lagi."]
    (out / "asal_jadwal.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\nHasil di {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())