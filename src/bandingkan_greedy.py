"""
bandingkan_greedy.py - Perbandingan hasil beberapa konfigurasi greedy (bahan Bab IV 4.4).

Membaca ringkasan_percobaan*.csv tiap folder greedy dan membandingkan:
  1. Kelayakan   : jumlah percobaan, percobaan sah (semua kelas terjadwal, 0 pelanggaran keras), persen sah,
                   kelas gagal (min, rata-rata, maks).
  2. Kualitas    : S1-S6 dan skor terpadu f_ref pada percobaan SAH (min, median, rata-rata, simpangan baku, terbaik + seed).
  3. Efek jumlah percobaan : f_ref terbaik setelah n percobaan pertama (n = 30, 100, 500, 1000, 5000, 10000 bila ada).
  4. Uji statistik (hanya pasangan dengan seed yang TIDAK tumpang tindih; pasangan yang tumpang tindih
     dikecualikan dan dicatat): Mann-Whitney (satu sisi bila diminta) pada f_ref, Cliff's delta, dan uji Fisher
     pada proporsi sah; koreksi Holm untuk semua uji.
  5. Grafik      : persen sah, boxplot f_ref, f_ref terbaik vs jumlah percobaan, sebaran kelas gagal (kriteria penalti).

Skala f_ref = rata-rata S1-S6 percobaan sah pada folder --skala (bawaan greedy5), sama dengan rekap_angka.py,
sehingga angkanya dapat dibandingkan dengan rekap dan Bab IV.

Keluaran (data\\perbandingan_greedy\\greedyBandingN, tidak menimpa):
  ringkasan_greedy.csv, uji_greedy.csv, kurva_percobaan.csv, perbandingan_greedy.txt, grafik_*.png, jejak.txt

Contoh (dari folder Penjadwalan_JTE):
  python src\\bandingkan_greedy.py --greedy greedy5 greedy8 greedy9 greedy10 --skala greedy5
"""
import argparse
import csv
import platform
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

BOBOT = dict(S1_gabung=1, S2_jeda=2, S3_beban=5, S4_sore=1, S5_merata=5, S6_utilisasi=3)  # sama dengan pemodelan.BOBOT
S = list(BOBOT)
TITIK = [30, 100, 500, 1000, 5000, 10000]


def baca(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def tulis(p, kepala, baris):
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(kepala)
        w.writerows(baris)


def fl(r, k):
    return float(str(r[k]).replace(",", "."))


def sah(r):
    return r["kelas_gagal"] == "0" and r["pelanggaran_keras"] == "0"


def cliff(x, y):
    """Cliff's delta (x vs y): negatif = x cenderung lebih kecil."""
    gt = sum(1 for a in x for b in y if a > b)
    lt = sum(1 for a in x for b in y if a < b)
    return (gt - lt) / (len(x) * len(y))


def label_delta(d):
    d = abs(d)
    return "dapat diabaikan" if d < 0.147 else "kecil" if d < 0.33 else "sedang" if d < 0.474 else "besar"


def holm(ps):
    n = len(ps)
    urut = sorted(range(n), key=lambda i: ps[i])
    out, maks = [None] * n, 0.0
    for r, i in enumerate(urut):
        maks = max(maks, min(1.0, (n - r) * ps[i]))
        out[i] = maks
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--greedy", nargs="+", required=True, help="folder greedy yang dibandingkan, mis. greedy5 greedy8 greedy9 greedy10")
    ap.add_argument("--skala", default="greedy5", help="folder greedy yang rata-rata jadwal sahnya menjadi skala f_ref")
    a = ap.parse_args()
    data = Path(a.data)
    try:
        from scipy.stats import fisher_exact, mannwhitneyu
    except ImportError:
        sys.exit("SciPy belum terpasang (pip install scipy).")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None

    # ---- muat
    sumber, grp = [], {}
    for g in a.greedy:
        fs = sorted((data / "greedy" / g).glob("ringkasan_percobaan*.csv"))
        if not fs:
            sys.exit(f"ringkasan_percobaan*.csv tidak ada di data\\greedy\\{g}")
        rows = baca(fs[0])
        if not rows or any(k not in rows[0] for k in S + ["seed", "kelas_gagal", "pelanggaran_keras"]):
            sys.exit(f"{g}: format lama (kolom S1-S6 tidak lengkap). Jalankan ulang greedy.py untuk folder ini.")
        sumber.append(fs[0])
        grp[g] = rows
    fs_skala = sorted((data / "greedy" / a.skala).glob("ringkasan_percobaan*.csv"))
    if not fs_skala:
        sys.exit(f"Folder skala {a.skala} tidak ditemukan.")
    sah_skala = [r for r in baca(fs_skala[0]) if sah(r)]
    if len(sah_skala) < 5:
        sys.exit("Skala dari kurang dari 5 jadwal sah: tidak stabil. Pilih --skala lain.")
    acuan = {k: st.mean(fl(r, k) for r in sah_skala) for k in S}

    def fref(r):
        return sum(BOBOT[k] * fl(r, k) / acuan[k] for k in S)

    N = 1
    while (data / "perbandingan_greedy" / f"greedyBanding{N}").exists():
        N += 1
    out = data / "perbandingan_greedy" / f"greedyBanding{N}"
    out.mkdir(parents=True)

    # ---- 1-2. ringkasan
    kepala = ["folder", "percobaan", "sah", "persen_sah", "gagal_min", "gagal_rata2", "gagal_maks", "waktu_rata2_detik",
              "fref_min", "fref_median", "fref_rata2", "fref_sd", "seed_terbaik"] + [f"{k}_rata2" for k in S]
    brs, fr, T = [], {}, []
    T.append("PERBANDINGAN KONFIGURASI GREEDY (Bab IV 4.4)")
    T.append(f"Dibuat: {datetime.now():%Y-%m-%d %H:%M}   Skala f_ref: rata-rata {len(sah_skala)} jadwal sah dari {a.skala}")
    T.append("f_ref = jumlah bobot x S_k / rata-rata_acuan_k (makin kecil makin baik; greedy skala = 17).")
    T.append("")
    for g, rows in grp.items():
        sg = [r for r in rows if sah(r)]
        gagal = [int(r["kelas_gagal"]) for r in rows]
        f = [fref(r) for r in sg]
        fr[g] = f
        terbaik = min(sg, key=fref)["seed"] if sg else "-"
        brs.append([g, len(rows), len(sg), round(100 * len(sg) / len(rows), 1), min(gagal), round(st.mean(gagal), 2), max(gagal),
                    round(st.mean(fl(r, "detik") for r in rows), 3),
                    round(min(f), 3) if f else "-", round(st.median(f), 3) if f else "-", round(st.mean(f), 3) if f else "-",
                    round(st.pstdev(f), 3) if f else "-", terbaik] + [round(st.mean(fl(r, k) for r in sg), 2) if sg else "-" for k in S])
        T.append(f"{g}: {len(rows)} percobaan, sah {len(sg)} ({100 * len(sg) / len(rows):.1f}%), kelas gagal min {min(gagal)} "
                 f"rata-rata {st.mean(gagal):.2f} maks {max(gagal)}"
                 + (f"; f_ref sah min {min(f):.3f} median {st.median(f):.3f} rata-rata {st.mean(f):.3f} (seed terbaik {terbaik})" if f
                    else "; tidak ada percobaan sah, f_ref tidak dihitung"))
    tulis(out / "ringkasan_greedy.csv", kepala, brs)

    # ---- 3. kurva percobaan
    kur, T2 = [], []
    for g, rows in grp.items():
        urut = sorted(rows, key=lambda r: int(r["seed"]))
        for n in TITIK:
            if n > len(urut):
                continue
            sub = [fref(r) for r in urut[:n] if sah(r)]
            kur.append([g, n, len(sub), round(min(sub), 3) if sub else "-"])
    tulis(out / "kurva_percobaan.csv", ["folder", "n_percobaan_pertama", "sah_di_dalamnya", "fref_terbaik"], kur)
    T += ["", "F_REF TERBAIK SETELAH n PERCOBAAN PERTAMA (urut seed)"]
    for g in grp:
        isi = [f"{r[1]}: {r[3]}" for r in kur if r[0] == g]
        if isi:
            T.append(f"  {g}: " + "; ".join(isi))

    # ---- 4. uji
    seeds = {g: {int(r["seed"]) for r in rows} for g, rows in grp.items()}
    uji, dilewati, catatan = [], [], []
    nama = list(grp)
    for i in range(len(nama)):
        for j in range(i + 1, len(nama)):
            x, y = nama[i], nama[j]
            bersama = seeds[x] & seeds[y]
            if bersama and len(bersama) >= 0.5 * max(len(seeds[x]), len(seeds[y])):
                dilewati.append((x, y, f"{len(bersama)} seed sama, sampel tidak independen"))
                continue
            rx, ry = grp[x], grp[y]
            if bersama:   # tumpang tindih kecil: buang seed bersama dari kelompok yang lebih besar
                besar = x if len(rx) >= len(ry) else y
                if besar == x:
                    rx = [r for r in rx if int(r["seed"]) not in bersama]
                else:
                    ry = [r for r in ry if int(r["seed"]) not in bersama]
                catatan.append(f"{x} vs {y}: {len(bersama)} seed sama dibuang dari {besar} agar sampel independen")
            sx, sy = [r for r in rx if sah(r)], [r for r in ry if sah(r)]
            fx, fy = [fref(r) for r in sx], [fref(r) for r in sy]
            p_sah = fisher_exact([[len(sx), len(rx) - len(sx)], [len(sy), len(ry) - len(sy)]])[1]
            uji.append([f"{x} vs {y}", "proporsi sah (Fisher)", len(rx), len(ry), "-", p_sah])
            if len(fx) >= 5 and len(fy) >= 5:
                p = mannwhitneyu(fx, fy, alternative="two-sided").pvalue
                d = cliff(fx, fy)
                uji.append([f"{x} vs {y}", f"f_ref sah (Mann-Whitney dua sisi); Cliff's delta {d:.3f} ({label_delta(d)})",
                            len(fx), len(fy), round(d, 3), p])
    ph = holm([u[5] for u in uji]) if uji else []
    tulis(out / "uji_greedy.csv", ["pasangan", "uji", "n_x", "n_y", "cliffs_delta", "p_mentah", "p_holm"],
          [u[:5] + [f"{u[5]:.4g}", f"{ph[k]:.4g}"] for k, u in enumerate(uji)])
    T += ["", "UJI STATISTIK (koreksi Holm atas seluruh uji pada tabel ini; ambang 0,05)"]
    for k, u in enumerate(uji):
        T.append(f"  {u[0]}: {u[1]}: p = {u[5]:.4g}, p Holm = {ph[k]:.4g}")
    for x, y, alasan in dilewati:
        T.append(f"  {x} vs {y}: TIDAK diuji ({alasan}); gunakan kurva percobaan.")
    for c in catatan:
        T.append("  Catatan: " + c)
    T += ["", "CARA MEMBACA",
          "  - 'Sah' berarti semua kelas terjadwal dan 0 pelanggaran keras. Kriteria penalti yang tidak menghasilkan percobaan sah tidak memiliki f_ref.",
          "  - Folder dengan seed yang sama (mis. 5000 percobaan adalah awalan dari 10000) tidak diuji karena bukan sampel independen.",
          "  - Cliff's delta negatif: kelompok pertama cenderung lebih kecil (lebih baik).",
          "  - Hasil hanya berlaku untuk data dan bobot yang dipakai; bobot S1-S6 belum dikalibrasi Ketua Jurusan."]
    (out / "perbandingan_greedy.txt").write_text("\n".join(T), encoding="utf-8")

    # ---- 5. grafik
    if plt:
        plt.rcParams.update({"font.size": 10})
        fig, ax = plt.subplots(figsize=(6, 3.6))
        ps = [100 * sum(1 for r in grp[g] if sah(r)) / len(grp[g]) for g in nama]
        b = ax.bar(nama, ps, color="#4C78A8")
        for r_, v in zip(b, ps):
            ax.text(r_.get_x() + r_.get_width() / 2, v + 1, f"{v:.1f}%", ha="center")
        ax.set_ylim(0, 110)
        ax.set_ylabel("Percobaan sah (%)")
        ax.set_title("Proporsi percobaan greedy yang sah")
        fig.tight_layout()
        fig.savefig(out / "grafik_persen_sah.png", dpi=200)
        plt.close(fig)
        ada = [g for g in nama if fr[g]]
        if ada:
            fig, ax = plt.subplots(figsize=(6, 3.8))
            ax.boxplot([fr[g] for g in ada], showfliers=True)
            ax.set_xticks(range(1, len(ada) + 1))
            ax.set_xticklabels(ada)
            ax.set_ylabel("f_ref (makin kecil makin baik)")
            ax.set_title(f"Sebaran f_ref percobaan sah (skala {a.skala})")
            fig.tight_layout()
            fig.savefig(out / "grafik_boxplot_fref.png", dpi=200)
            plt.close(fig)
        if kur:
            fig, ax = plt.subplots(figsize=(6, 3.8))
            for g in nama:
                pts = [(r[1], r[3]) for r in kur if r[0] == g and r[3] != "-"]
                if pts:
                    ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label=g)
            ax.set_xscale("log")
            ax.set_xlabel("Jumlah percobaan pertama (n)")
            ax.set_ylabel("f_ref terbaik")
            ax.set_title("Efek menambah jumlah percobaan greedy")
            ax.legend()
            fig.tight_layout()
            fig.savefig(out / "grafik_kurva_percobaan.png", dpi=200)
            plt.close(fig)
        tanpa = [g for g in nama if not fr[g]]
        if tanpa:
            fig, ax = plt.subplots(figsize=(6, 3.6))
            for g in tanpa:
                ax.hist([int(r["kelas_gagal"]) for r in grp[g]], bins=range(0, 60, 3), alpha=0.8, label=g)
            ax.set_xlabel("Jumlah kelas gagal dijadwalkan per percobaan")
            ax.set_ylabel("Jumlah percobaan")
            ax.set_title("Sebaran kelas gagal (tanpa percobaan sah)")
            ax.legend()
            fig.tight_layout()
            fig.savefig(out / "grafik_kelas_gagal.png", dpi=200)
            plt.close(fig)

    # ---- jejak
    J = ["JEJAK BANDINGKAN GREEDY", f"Waktu: {datetime.now():%Y-%m-%d %H:%M:%S}", f"Perintah: {' '.join(sys.argv)}",
         f"Python {platform.python_version()}", f"Skala acuan: {a.skala} ({len(sah_skala)} jadwal sah)",
         "Rumus: f_ref = sum(bobot_k * S_k / rata2_acuan_k); bobot " + str(BOBOT), "", "Berkas sumber:"]
    for s in sumber + [fs_skala[0]]:
        J.append(f"  {s}  ({s.stat().st_size} B, diubah {datetime.fromtimestamp(s.stat().st_mtime):%Y-%m-%d %H:%M})")
    (out / "jejak.txt").write_text("\n".join(J), encoding="utf-8")
    print("\n".join(T))
    print(f"\nHasil di {out}")


if __name__ == "__main__":
    main()