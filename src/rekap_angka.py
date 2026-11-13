"""
rekap_angka.py - Rekap SEMUA angka laporan dari berkas mentah dengan SATU skala f_ref dan jejak sumber.

Mengapa ada: f_ref bergantung pada skala acuan. Bila tiap skrip memakai skala berbeda, angka tidak dapat dibandingkan.
Skrip ini menetapkan satu skala (rata-rata semua percobaan greedy yang lengkap dan sah pada SATU folder greedy),
menghitung ulang f_ref untuk semua konfigurasi GA dan greedy, lalu menulis tiap angka beserta rumus dan berkas sumbernya.
Skrip hanya MEMBACA data\\greedy dan data\\ga; hasil ditulis ke folder baru data\\rekap\\rekapN (tidak menimpa).

Contoh:
  python src\\rekap_angka.py --skala greedy5 --greedy greedy1 greedy5 greedy8 greedy9 greedy10 --ga ga13 ga15 ga16

Keluaran (data\\rekap\\rekapN):
  skala_acuan.csv   : rata-rata S1-S6 yang dipakai sebagai pembagi, jumlah jadwal, folder sumber
  angka_greedy.csv  : tiap folder greedy: percobaan, percobaan sah, kelas gagal, f_ref (min, median, rata-rata)
  angka_ga.csv      : tiap folder GA: ulangan sah, f_ref awal dan akhir (min, median), seed terbaik, p Wilcoxon, waktu
  jejak.txt         : perintah, waktu, versi Python/SciPy, daftar berkas sumber beserta ukuran dan tanggal ubahnya
"""
import argparse
import csv
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

BOBOT = dict(S1_gabung=1, S2_jeda=2, S3_beban=5, S4_sore=1, S5_merata=5, S6_utilisasi=3)  # sama dengan pemodelan.BOBOT
S = list(BOBOT)


def baca(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def tulis(p, kepala, baris):
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(kepala)
        w.writerows(baris)


def angka(d, k):
    return float(str(d[k]).replace(",", "."))


def cari(folder, pola):
    c = sorted(Path(folder).glob(pola))
    if not c:
        sys.exit(f"Berkas {pola} tidak ada di {folder}")
    return c[0]


def sah_greedy(rows):
    return [r for r in rows if r["kelas_gagal"] == "0" and r["pelanggaran_keras"] == "0"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--skala", required=True, help="folder greedy (mis. greedy5) yang rata-rata jadwal sahnya dijadikan skala")
    ap.add_argument("--greedy", nargs="*", default=[])
    ap.add_argument("--ga", nargs="*", default=[])
    a = ap.parse_args()
    data = Path(a.data)
    sumber = []

    # ---- skala acuan
    fs = data / "greedy" / a.skala
    p = cari(fs, "ringkasan_percobaan*.csv")
    sumber.append(p)
    sah = sah_greedy(baca(p))
    if len(sah) < 5:
        sys.exit(f"Skala dari {len(sah)} jadwal sah saja (< 5): tidak stabil. Pilih folder greedy lain.")
    acuan = {k: max(1e-9, st.mean(angka(r, k) for r in sah)) for k in S}

    def fref(r, awal=False):
        return sum(BOBOT[k] * angka(r, ("awal_" + k) if awal else k) / acuan[k] for k in S)

    N = 1
    while (data / "rekap" / f"rekap{N}").exists():
        N += 1
    out = data / "rekap" / f"rekap{N}"
    out.mkdir(parents=True)

    tulis(out / "skala_acuan.csv", ["suku", "bobot", "rata_rata_acuan", "n_jadwal_sah", "folder_sumber", "berkas"],
          [[k, BOBOT[k], round(acuan[k], 4), len(sah), a.skala, p.name] for k in S])

    # ---- greedy
    brs = []
    for g in a.greedy:
        p = cari(data / "greedy" / g, "ringkasan_percobaan*.csv")
        sumber.append(p)
        rows = baca(p)
        if not rows or any(k not in rows[0] for k in S + ["kelas_gagal", "pelanggaran_keras"]):
            brs.append([g, "-", "-", "-", "-", "-", "-", "-", "-", "-", p.name + " (format lama, kolom tidak lengkap: dilewati)"])
            continue
        sg = sah_greedy(rows)
        gagal = [int(r["kelas_gagal"]) for r in rows]
        fr = [fref(r) for r in sg]
        brs.append([g, len(rows), len(sg), round(100 * len(sg) / len(rows), 1), min(gagal), round(st.mean(gagal), 2), max(gagal),
                    round(min(fr), 3) if fr else "-", round(st.median(fr), 3) if fr else "-", round(st.mean(fr), 3) if fr else "-",
                    p.name])
    tulis(out / "angka_greedy.csv", ["folder", "percobaan", "sah", "persen_sah", "gagal_min", "gagal_rata2", "gagal_maks",
                                     "fref_min", "fref_median", "fref_rata2", "berkas_sumber"], brs)

    # ---- GA
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        wilcoxon = None
    brs = []
    for g in a.ga:
        p = cari(data / "ga" / g, "ringkasan_ulangan*.csv")
        sumber.append(p)
        rows = baca(p)
        if not rows or any(("awal_" + k) not in rows[0] for k in S):
            print(f"{g}: format lama (tanpa kolom awal_S*), dilewati")
            continue
        sg = [r for r in rows if r["pelanggaran_keras"] == "0"]
        fa = [fref(r) for r in sg]
        fw = [fref(r, awal=True) for r in sg]
        terbaik = min(range(len(sg)), key=lambda i: fa[i]) if sg else None
        pw = "-"
        if wilcoxon and sg and any(x != y for x, y in zip(fw, fa)):
            pw = f"{wilcoxon(fw, fa, alternative='greater').pvalue:.3g}"
        elif sg:
            pw = "1 (tidak ada perubahan)"
        brs.append([g, len(rows), len(sg), round(min(fw), 3), round(st.median(fw), 3), round(min(fa), 3), round(st.median(fa), 3),
                    round(st.mean(fa), 3), sg[terbaik]["seed"], sum(1 for x, y in zip(fw, fa) if y < x - 1e-9), pw,
                    round(st.median(angka(r, "detik") for r in sg), 1), p.name])
    tulis(out / "angka_ga.csv", ["folder", "ulangan", "sah", "fref_awal_min", "fref_awal_median", "fref_akhir_min", "fref_akhir_median",
                                 "fref_akhir_rata2", "seed_f_ref_terendah", "run_membaik", "p_wilcoxon_awal_vs_akhir", "waktu_median_detik",
                                 "berkas_sumber"], brs)

    # ---- jejak
    import platform
    L = ["JEJAK REKAP ANGKA", f"Waktu: {datetime.now():%Y-%m-%d %H:%M:%S}", f"Perintah: {' '.join(sys.argv)}",
         f"Python {platform.python_version()}" + (f", SciPy sudah terpasang" if wilcoxon else ", SciPy TIDAK terpasang (p tidak dihitung)"),
         f"Skala acuan: rata-rata {len(sah)} jadwal greedy sah dari {a.skala}",
         "Rumus: f_ref = sum(bobot_k * S_k / rata2_acuan_k), k = S1..S6; makin kecil makin baik.",
         "PERINGATAN: f_ref dari skrip lain (kandidat_jadwal, bandingkan_jadwal) dapat memakai skala berbeda; jangan dicampur.", "",
         "Berkas sumber:"]
    for s in sumber:
        stt = s.stat()
        L.append(f"  {s}  ({stt.st_size} B, diubah {datetime.fromtimestamp(stt.st_mtime):%Y-%m-%d %H:%M})")
    (out / "jejak.txt").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nHasil di {out}")


if __name__ == "__main__":
    main()