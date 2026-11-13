"""
Tahap 5 - Perbandingan greedy dan GA (bahan Bab 4): statistik, uji statistik, dan grafik.

Membaca keluaran greedy.py (ringkasan_percobaan) dan satu atau lebih keluaran ga.py (ringkasan_ulangan),
semuanya dari data pembersihan yang SAMA. Dasar perbandingan: hanya run yang sah (0 pelanggaran keras, semua kelas terjadwal).

Skor terpadu f_ref: tiap suku lunak dibagi rata-ratanya pada percobaan greedy yang sah (skala acuan TETAP), lalu dibobot
dengan BOBOT dan dijumlahkan. Skala acuan sama untuk semua metode, sehingga f_ref adil. (Biaya internal GA memakai skala
dari populasi awal tiap run, yang berbeda antar run, karena itu tidak dipakai untuk membandingkan metode.)

Uji statistik (scipy):
  - greedy vs tiap konfigurasi GA : Mann-Whitney U dua sisi (dua sampel independen, tanpa asumsi normal), plus Cliff's delta
    sebagai ukuran efek (|d| < 0,147 diabaikan, < 0,33 kecil, < 0,474 sedang, selebihnya besar; Romano dkk. 2006).
  - GA vs populasi awalnya sendiri : Wilcoxon signed-rank (data berpasangan: biaya awal terbaik vs biaya akhir tiap run).
Catatan: p-value tidak dikoreksi untuk uji berganda; baca bersama ukuran efek.

Contoh:
  python src/bandingkan.py --greedy data/greedy/greedy1 --ga data/ga/ga1 data/ga/ga2 data/ga/ga3
  python src/bandingkan.py                       # otomatis: greedy terbaru + GA terbaru tiap LABEL pada sumber yang sama

Keluaran: data/perbandingan/perbandinganN/ : ringkasan_metode.csv, uji_statistik.csv, perbandingan.txt, grafik_*.png
"""
import argparse
import csv
import re
import statistics as st
import sys
from pathlib import Path

import numpy as np

from pemodelan import BOBOT, SUKU, baca, nomor_berikut, tulis

NAMA_SUKU = [n for n, _ in SUKU]


def _angka(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _status(folder):
    d = {}
    for p in Path(folder).glob("status*.txt"):
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                d[k.strip()] = v.strip()
    return d


def baca_greedy(folder):
    rows = [r for p in Path(folder).glob("ringkasan_percobaan*.csv") for r in baca(p)]
    sah = [r for r in rows if _angka(r["kelas_gagal"]) == 0 and _angka(r["pelanggaran_keras"]) == 0]
    return len(rows), [{k: _angka(v) for k, v in r.items()} for r in sah]


def baca_ga(folder):
    rows = []
    wajib = set(NAMA_SUKU) | {"pelanggaran_keras"}
    for p in Path(folder).glob("ringkasan_ulangan*.csv"):
        rr = baca(p)
        if rr and not wajib <= set(rr[0]):   # CSV format lama (kolom suku tidak lengkap) dilewati
            continue
        rows += rr
    sah = [r for r in rows if _angka(r["pelanggaran_keras"]) == 0]
    return len(rows), [{k: _angka(v) for k, v in r.items()} for r in sah]


def kotak(ax, data, label, **kw):
    """boxplot yang kompatibel dengan matplotlib lama (labels) dan baru (tick_labels)."""
    try:
        return ax.boxplot(data, tick_labels=label, **kw)
    except TypeError:
        return ax.boxplot(data, labels=label, **kw)


def cliffs_delta(x, y):
    """Peluang x > y dikurangi peluang x < y; negatif berarti x cenderung lebih kecil (lebih baik, karena biaya)."""
    gt = sum(1 for a in x for b in y if a > b)
    lt = sum(1 for a in x for b in y if a < b)
    return (gt - lt) / (len(x) * len(y))


def arti_delta(d):
    d = abs(d)
    return "diabaikan" if d < 0.147 else "kecil" if d < 0.33 else "sedang" if d < 0.474 else "besar"


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Perbandingan greedy vs GA")
    ap.add_argument("--greedy", help="folder greedyN (bawaan: terbaru)")
    ap.add_argument("--ga", nargs="*", help="folder gaN (bawaan: terbaru tiap LABEL pada sumber yang sama)")
    ap.add_argument("--keluar", default=str(base / "perbandingan"))
    a = ap.parse_args()

    gdir = Path(a.greedy) if a.greedy else None
    if gdir is None:
        cand = [p for p in (base / "greedy").glob("greedy*") if re.fullmatch(r"greedy\d+", p.name)]
        if not cand:
            sys.exit("Tidak ada folder greedyN. Jalankan src/greedy.py dulu.")
        gdir = max(cand, key=lambda p: int(p.name[6:]))
    sumber = _status(gdir).get("SUMBER")
    if a.ga:
        gadirs = [Path(p) for p in a.ga]
    else:
        terbaru = {}
        for p in (base / "ga").glob("ga*"):
            if re.fullmatch(r"ga\d+", p.name):
                s = _status(p)
                if s.get("SUMBER") == sumber:
                    lab = s.get("LABEL") or p.name
                    if lab not in terbaru or int(p.name[2:]) > int(terbaru[lab].name[2:]):
                        terbaru[lab] = p
        gadirs = [terbaru[k] for k in sorted(terbaru)]
    if not gadirs:
        sys.exit("Tidak ada folder gaN yang cocok. Jalankan src/ga.py dulu.")

    n_g, sah_g = baca_greedy(gdir)
    if not sah_g:
        sys.exit("Tidak ada percobaan greedy yang sah; perbandingan tidak dapat dihitung.")
    metode = {"greedy": (n_g, sah_g)}
    asal = {"greedy": gdir}
    awal_akhir = {}
    for p in gadirs:
        s = _status(p)
        if s.get("SUMBER") != sumber:
            print(f"PERINGATAN: {p.name} memakai sumber {s.get('SUMBER')}, greedy memakai {sumber}.")
        lab = s.get("LABEL") or p.name
        if f"GA:{lab}" in metode:
            lab = f"{lab}_{p.name}"
        n, sah = baca_ga(p)
        metode[f"GA:{lab}"] = (n, sah)
        asal[f"GA:{lab}"] = p
        awal_akhir[f"GA:{lab}"] = [(r["biaya_awal_terbaik"], r["biaya_akhir"]) for r in sah if "biaya_awal_terbaik" in r]
        # populasi awal GA = yang terbaik dari ukuran-populasi sampel greedy: pembanding yang setara usahanya
        semua_baris = [{k: _angka(v) for k, v in r.items()} for q in Path(p).glob("ringkasan_ulangan*.csv") for r in baca(q)]
        awal_sah = []
        for r in semua_baris:
            if "awal_pelanggaran_keras" in r and r["awal_pelanggaran_keras"] == 0:
                x = {n: r[f"awal_{n}"] for n in NAMA_SUKU}
                x["f_berbobot"] = r["awal_f_berbobot"]
                x["seed"] = r["seed"]
                awal_sah.append(x)
        if awal_sah:
            metode[f"GA:{lab} [populasi awal]"] = (n, awal_sah)
            asal[f"GA:{lab} [populasi awal]"] = p

    # skala acuan tetap = rata-rata greedy sah
    acuan = {n: max(1e-9, st.mean(r[n] for r in sah_g)) for n in NAMA_SUKU}

    def f_ref(r):
        return sum(BOBOT[k] * r[n] / acuan[n] for n, k in SUKU)

    metrik = {"f_ref": f_ref, "f_berbobot": lambda r: r["f_berbobot"] if "f_berbobot" in r else r["f_berbobot_mentah"]}
    for n in NAMA_SUKU:
        metrik[n] = (lambda nn: lambda r: r[nn])(n)
    metrik["detik"] = lambda r: r.get("detik", float("nan"))
    metrik["generasi"] = lambda r: r.get("generasi", float("nan"))
    nilai = {m: {mt: [fn(r) for r in sah] for mt, fn in metrik.items()} for m, (n, sah) in metode.items()}

    keluar = Path(a.keluar)
    keluar.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(keluar, "perbandingan")
    out = keluar / f"perbandingan{N}"
    out.mkdir()

    # Populasi awal identik untuk semua konfigurasi GA (seed sama), jadi hanya satu baris yang ditampilkan.
    # Semua entri tetap dipakai di dalam untuk uji berpasangan.
    awal_semua = [m for m in metode if m.endswith("[populasi awal]")]
    tampil = [m for m in metode if not m.endswith("[populasi awal]")]
    if awal_semua:
        tampil.insert(1, awal_semua[0])

    def tag(m):
        return "populasi awal GA (greedy terbaik dari 20)" if m.endswith("[populasi awal]") else m

    # ---- ringkasan per metode
    baris = []
    for m in tampil:
        n, sah = metode[m]
        for mt in metrik:
            xs = [x for x in nilai[m][mt] if x == x]
            if not xs:
                continue
            baris.append([tag(m), mt, len(sah), n, round(st.mean(xs), 4), round(st.pstdev(xs), 4), round(min(xs), 4),
                          round(st.median(xs), 4), round(max(xs), 4)])
    tulis(out / f"ringkasan_metode{N}.csv", ["metode", "metrik", "run_sah", "run_total", "rata2", "simpangan_baku", "min", "median", "maks"], baris)

    # ---- uji statistik
    from scipy import stats
    uji = []
    pasang_fref = {}   # m -> (f_ref populasi awal, f_ref akhir) pada seed yang sama, untuk grafik uji
    for m in metode:
        if m == "greedy" or (m in awal_semua and m != awal_semua[0]):
            continue
        for mt in ["f_ref", "f_berbobot"] + NAMA_SUKU:
            x, y = nilai["greedy"][mt], nilai[m][mt]
            if len(x) < 2 or len(y) < 2:
                continue
            try:
                u, p = stats.mannwhitneyu(x, y, alternative="two-sided")
            except ValueError:
                u, p = float("nan"), 1.0
            d = cliffs_delta(y, x)   # y = GA; d < 0 berarti GA cenderung lebih kecil (lebih baik) daripada greedy
            uji.append(["greedy vs " + tag(m), mt, len(x), len(y), round(st.median(x), 4), round(st.median(y), 4), round(u, 2),
                        round(p, 6), round(d, 3), arti_delta(d)])
        if m.endswith("[populasi awal]"):
            continue
        # uji berpasangan pada f_ref: populasi awal vs hasil akhir pada run yang sama (kontribusi GA yang sebenarnya)
        awal_m = {r["seed"]: r for r in metode[m + " [populasi awal]"][1]} if (m + " [populasi awal]") in metode else {}
        akhir_m = {r["seed"]: r for r in metode[m][1]}
        sd_sama = [sd for sd in akhir_m if sd in awal_m]
        if len(sd_sama) >= 2:
            fa = [f_ref(awal_m[sd]) for sd in sd_sama]
            fz = [f_ref(akhir_m[sd]) for sd in sd_sama]
            pasang_fref[m] = (fa, fz)
            berubah = sum(1 for x, y in zip(fa, fz) if abs(x - y) > 1e-9)
            if berubah >= 2:
                try:
                    w, p = stats.wilcoxon(fa, fz, alternative="greater")
                except ValueError:
                    w, p = float("nan"), 1.0
            else:
                w, p = float("nan"), 1.0
            uji.append([m + ": f_ref populasi awal > hasil akhir (Wilcoxon berpasangan, satu sisi)", "f_ref", len(sd_sama), len(sd_sama),
                        round(st.median(fa), 4), round(st.median(fz), 4), round(w, 2) if w == w else "", round(p, 6), "",
                        f"{berubah} dari {len(sd_sama)} run berubah; median beda {st.median(x - y for x, y in zip(fa, fz)):.4f}"])
        pasang = awal_akhir.get(m, [])
        beda = [(b, c) for b, c in pasang if abs(b - c) > 1e-12]
        if len(pasang) >= 2:
            if beda and len(beda) >= 2:
                try:
                    w, p = stats.wilcoxon([b for b, _ in pasang], [c for _, c in pasang], alternative="greater")
                except ValueError:
                    w, p = float("nan"), 1.0
            else:
                w, p = float("nan"), 1.0
            uji.append([m + ": biaya awal terbaik > biaya akhir (Wilcoxon, satu sisi)", "biaya_internal", len(pasang), len(pasang),
                        round(st.median(b for b, _ in pasang), 4), round(st.median(c for _, c in pasang), 4), round(w, 2) if w == w else "",
                        round(p, 6), "", f"{len(beda)} dari {len(pasang)} run berubah"])
    tulis(out / f"uji_statistik{N}.csv",
          ["pasangan", "metrik", "n_pertama", "n_kedua", "median_pertama", "median_kedua", "statistik", "p_value", "cliffs_delta_GA_vs_greedy", "arti_efek"], uji)

    # ---- teks ringkasan
    t = ["PERBANDINGAN GREEDY DAN GA", f"Sumber data: {sumber}", f"Greedy: {gdir}", ""]
    t.append("Run sah / total, dan skor terpadu f_ref (makin kecil makin baik; skala acuan = rata-rata greedy sah):")
    for m in tampil:
        n, sah = metode[m]
        xs = nilai[m]["f_ref"]
        if xs:
            t.append(f"  {tag(m):42s} sah {len(sah):3d}/{n:<3d}  f_ref rata-rata {st.mean(xs):.3f}  sd {st.pstdev(xs):.3f}  "
                     f"min {min(xs):.3f}  median {st.median(xs):.3f}")
        else:
            t.append(f"  {tag(m):42s} sah {len(sah):3d}/{n:<3d}  (tidak ada run sah)")
    t += ["", "Uji statistik pada f_ref (p < 0,05 menandakan beda nyata; baca bersama ukuran efek):"]
    for r in uji:
        if r[1] in ("f_ref", "biaya_internal"):
            t.append(f"  {r[0]}: p = {r[7]}" + (f", Cliff's delta = {r[8]} ({r[9]})" if r[8] != "" else f" ({r[9]})"))
    t += ["", "Cara membaca:",
          "  - Cliff's delta negatif: GA cenderung memberi biaya lebih kecil (lebih baik) daripada greedy; positif: lebih buruk.",
          "  - Bila GA tidak sah atau tidak membaik dari populasi awal, kesimpulan 'GA lebih baik' tidak boleh ditarik.",
          "  - PERINGATAN: 'greedy' = satu sampel per percobaan, sedangkan populasi awal GA = yang terbaik dari sejumlah sampel greedy.",
          "    Maka 'greedy vs GA' menguntungkan GA. Kontribusi GA yang sebenarnya hanya terlihat pada uji berpasangan",
          "    'populasi awal > hasil akhir' (run yang sama). Baris 'greedy vs populasi awal GA' menunjukkan efek memilih yang terbaik, bukan GA.",
          "  - Hasil hanya berlaku untuk data dan bobot yang dipakai; bobot S1-S6 adalah titik awal, bukan hasil kalibrasi Kajur."]
    (out / f"perbandingan{N}.txt").write_text("\n".join(t) + "\n", encoding="utf-8")
    print("\n".join(t))

    # ---- grafik
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib tidak ada; grafik dilewati (pip install matplotlib)")
        plt = None
    if plt:
        nama = list(tampil)

        def pendek(m):
            return tag(m).replace('GA:', '').replace(' (greedy terbaik dari 20)', '\n(greedy terbaik\ndari 20)').replace('_', '\n')
        # 1) boxplot f_ref
        fig, ax = plt.subplots(figsize=(max(7, 2.2 * len(nama)), 5))
        data = [nilai[m]["f_ref"] for m in nama]
        kotak(ax, [d if d else [np.nan] for d in data], [pendek(m) for m in nama], showmeans=True)
        for i, d in enumerate(data, start=1):
            ax.scatter(np.random.default_rng(1).normal(i, 0.04, len(d)), d, s=10, alpha=0.5)
        ax.set_ylabel("f_ref (makin kecil makin baik)")
        ax.set_title("Skor terpadu f_ref pada run yang sah")
        fig.tight_layout()
        fig.savefig(out / f"grafik_boxplot_fref{N}.png", dpi=150)
        plt.close(fig)
        # 2) boxplot tiap suku lunak
        fig, axs = plt.subplots(2, 3, figsize=(16, 8))
        for ax, nn in zip(axs.ravel(), NAMA_SUKU):
            kotak(ax, [nilai[m][nn] if nilai[m][nn] else [np.nan] for m in nama], [pendek(m) for m in nama], showmeans=True)
            ax.set_title(nn)
            ax.tick_params(axis="x", labelrotation=0, labelsize=6)
        fig.suptitle("Suku lunak mentah pada run yang sah")
        fig.tight_layout()
        fig.savefig(out / f"grafik_suku_lunak{N}.png", dpi=150)
        plt.close(fig)
        # 3) konvergensi (run terbaik tiap konfigurasi GA)
        fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
        for m in nama:
            if m == "greedy" or m.endswith("[populasi awal]"):
                continue
            rp = list(Path(asal[m]).glob("riwayat_biaya*.csv"))
            if not rp:
                continue
            rows = baca(rp[0])
            gen = [int(r["generasi"]) for r in rows]
            axs[0].plot(gen, [_angka(r["biaya_terbaik"]) for r in rows], label=m)
            axs[1].plot(gen, [_angka(r["biaya_rata2_populasi"]) for r in rows], label=m)
        axs[0].set_title("Biaya terbaik per generasi (run terbaik)")
        axs[1].set_title("Biaya rata-rata populasi per generasi")
        axs[1].set_yscale("log")
        for ax in axs:
            ax.set_xlabel("generasi")
            ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out / f"grafik_konvergensi{N}.png", dpi=150)
        plt.close(fig)
        # 4) waktu, iterasi, dan trade-off kualitas-waktu per run
        ga_m = [m for m in nama if m.startswith("GA:") and not m.endswith("[populasi awal]")]
        run_m = [m for m in nama if nilai[m]["detik"] and not all(x != x for x in nilai[m]["detik"])]
        fig, axs = plt.subplots(1, 3, figsize=(17, 5))
        data_t = [[x for x in nilai[m]["detik"] if x == x] for m in run_m]
        kotak(axs[0], data_t, [pendek(m) for m in run_m], showmeans=True)
        axs[0].set_yscale("log")
        axs[0].set_ylabel("detik per run (skala log)")
        axs[0].set_title("Waktu komputasi per run")
        axs[0].tick_params(axis="x", labelsize=7)
        data_g = [[x for x in nilai[m]["generasi"] if x == x] for m in ga_m]
        if any(data_g):
            kotak(axs[1], [d if d else [np.nan] for d in data_g], [pendek(m) for m in ga_m], showmeans=True)
        axs[1].set_ylabel("generasi yang dijalankan")
        axs[1].set_title("Iterasi (generasi) sampai berhenti")
        axs[1].tick_params(axis="x", labelsize=7)
        for i, m in enumerate(run_m):
            xs = [r.get("detik", float("nan")) for r in metode[m][1]]
            ys = nilai[m]["f_ref"]
            axs[2].scatter(xs, ys, s=14, alpha=0.6, label=tag(m))
        axs[2].set_xscale("log")
        axs[2].set_xlabel("detik per run (skala log)")
        axs[2].set_ylabel("f_ref (makin kecil makin baik)")
        axs[2].set_title("Kualitas terhadap waktu, satu titik per run")
        axs[2].legend(fontsize=7)
        fig.suptitle("Waktu dan iterasi: greedy, GA, (populasi awal GA tidak punya waktu tersendiri, dibentuk dari 20 run greedy)", fontsize=9)
        fig.tight_layout()
        fig.savefig(out / f"grafik_waktu_iterasi{N}.png", dpi=150)
        plt.close(fig)
        # 5) uji statistik pada f_ref: ukuran efek (Cliff's delta) dan nilai p
        komp = []
        for r in uji:
            if r[1] != "f_ref":
                continue
            if r[0].startswith("greedy vs "):
                komp.append((r[0].replace("greedy vs ", "greedy vs ").replace(" (greedy terbaik dari 20)", ""), r[8], r[7], "greedy vs sampel GA"))
            elif "Wilcoxon berpasangan" in r[0]:
                m = r[0].split(": f_ref")[0]
                if m in pasang_fref:
                    fa, fz = pasang_fref[m]
                    komp.append((m.replace("GA:", "") + ": populasi awal vs akhir (berpasangan)", cliffs_delta(fz, fa), r[7], "kontribusi GA"))
        if komp:
            fig, ax = plt.subplots(figsize=(11, 0.55 * len(komp) + 2))
            warna = {"greedy vs sampel GA": "#9a9a9a", "kontribusi GA": "#2f6fb0"}
            for i, (lab, d, p_, jenis) in enumerate(komp):
                ax.barh(i, d, color=warna[jenis])
                ptxt = "p < 0,001" if p_ < 0.001 else "p = " + f"{p_:.3f}".replace(".", ",")
                ax.text(0.03, i, f"delta = {d:.2f}".replace(".", ",") + f", {ptxt}", va="center", fontsize=8)
            ax.set_yticks(range(len(komp)))
            ax.set_yticklabels([k[0] for k in komp], fontsize=8)
            ax.invert_yaxis()
            ax.axvline(0, color="black", lw=0.8)
            ax.set_xlim(-1.05, 0.9)
            ax.set_xlabel("Cliff's delta pada f_ref (negatif = nilai kedua lebih kecil = lebih baik)")
            ax.set_title("Uji statistik f_ref: abu-abu = greedy vs GA (condong ke GA), biru = kontribusi GA yang sebenarnya")
            fig.tight_layout()
            fig.savefig(out / f"grafik_uji_statistik{N}.png", dpi=150)
            plt.close(fig)
    print(f"Hasil di {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())