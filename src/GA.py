"""
Tahap 3b - Algoritma genetika dengan PyGAD (Gad, 2023), mengikuti Bab 3 skripsi (Subbab 3.3.5, Tabel 7).

Semua operator evolusi adalah BAWAAN PyGAD: steady-state selection (sss), single-point crossover,
random mutation (mutation_percent_genes), keep_elitism, stop_criteria saturate_N. Logika khusus masalah
(domain, fitness, repair, validator) berada di luar PyGAD dan dipanggil lewat fitness_func, on_crossover, on_mutation.

Fitness (Bab 3): P(C) = M (U(C) + H(C)) + S(C);  F(C) = 1 / (1 + P(C)).  U(C) = 0 karena tiap gen selalu menunjuk
satu alternatif domain. M = 1000. [IMPROVISASI] S(C) memakai S1-S4 dari pemodelan yang dinormalkan dengan rata-rata
populasi awal, agar syarat Bab 3 "M lebih besar daripada rentang penalti soft" terpenuhi (tanpa normalisasi,
S mentah pada data ini berorde ribuan sehingga M = 1000 tidak lagi dominan).

Bawaan = Tabel 7 Bab 3: populasi 20, 3000 generasi, 10 induk, sss, single_point, random mutation 10% gen, elitism 1,
saturate_200, M = 1000, populasi awal dari greedy (greedy.py).

Contoh:
  python src/ga.py                                  # konfigurasi Bab 3: 30 run, seed 5000-5029 (lama!)
  python src/ga.py --ulangan 1 --generasi 300       # uji cepat satu run
  python src/ga.py --awal acak                      # populasi awal acak (tanpa greedy), untuk pembanding
  python src/ga.py --tanpa-repair                   # GA tanpa repair, untuk pembanding
  python src/ga.py --ulangan 5                      # jumlah run lain
"""
import argparse
import csv
from collections import defaultdict
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import pygad

# GA ini tidak memakai greedy kecuali dengan --awal greedy (hibrida).
from laporan import buat_laporan
from pemodelan import BOBOT, SUKU, Keadaan, Masalah, nomor_berikut, tulis, tulis_jadwal


class Evaluasi:
    """Penghubung antara kromosom (indeks domain tiap kelas) dan model masalah."""

    def __init__(self, ms, M=1000.0):
        self.ms = ms
        self.kids = list(ms.kelas)
        self.indeks = {kid: {opt: i for i, opt in enumerate(ms.domain[kid])} for kid in self.kids}
        self.skala = {n: 1.0 for n, _ in SUKU}
        self.M = M
        self.cache = {}

    # ---- kromosom <-> jadwal
    def dekode(self, sol):
        return {kid: self.ms.domain[kid][int(g)] for kid, g in zip(self.kids, sol)}

    def enkode(self, jadwal, rng):
        """Jadwal parsial -> kromosom; kelas yang gagal ditempatkan diberi nilai acak dari domainnya."""
        return np.array([self.indeks[kid][jadwal[kid]] if kid in jadwal else rng.randrange(len(self.ms.domain[kid]))
                         for kid in self.kids], dtype=int)

    # ---- biaya
    def tetapkan_skala(self, populasi):
        """[IMPROVISASI] Skala tiap suku lunak = rata-rata nilainya pada populasi awal, agar bobot w1..w4 sebanding."""
        tot = {k: 0.0 for k in self.skala}
        for sol in populasi:
            lunak = self.ms.validasi(self.dekode(sol), detail=False)[1]
            for k in tot:
                tot[k] += lunak[k]
        for k in tot:
            self.skala[k] = max(1.0, tot[k] / len(populasi))

    def komponen(self, sol):
        v, lunak, _ = self.ms.validasi(self.dekode(sol), detail=False)
        pel = sum(v.values())
        lunak_norm = sum(BOBOT[k] * lunak[n] / self.skala[n] for n, k in SUKU)
        return pel, lunak_norm, lunak, v

    def biaya(self, sol):
        """P(C) = M (U + H) + S (Bab 3); U = 0. Fitness PyGAD = 1 / (1 + P)."""
        kunci = sol.tobytes()
        b = self.cache.get(kunci)
        if b is None:
            pel, lunak_norm, _, _ = self.komponen(sol)
            b = lunak_norm + self.M * pel
            if len(self.cache) > 60000:
                self.cache.clear()
            self.cache[kunci] = b
        return b


def populasi_awal(ms, ev, n, seed, mode, kandidat=30):
    """mode 'greedy' (bawaan, Bab 3 butir b): tiap individu dibangun oleh greedy.py dengan seed berbeda.
    mode 'acak': tiap gen acak dari domainnya (pembanding: GA tanpa bantuan greedy)."""
    rng = random.Random(seed)
    pop, gagal_total = [], 0
    if mode == "greedy":
        from greedy import bangun
        for i in range(n):
            jd, gagal = bangun(ms, random.Random(seed * 100003 + i), kandidat=kandidat)
            gagal_total += len(gagal)
            pop.append(ev.enkode(jd, rng))
    else:
        for _ in range(n):
            pop.append(ev.enkode({}, rng))
    return np.array(pop, dtype=int), gagal_total


def jalankan(ms, a, seed, log):
    ev = Evaluasi(ms, a.M)
    pop0, gagal_awal = populasi_awal(ms, ev, a.populasi, seed, a.awal, a.kandidat)
    ev.tetapkan_skala(pop0)
    unik = len({p.tobytes() for p in pop0})
    base = min(pop0, key=ev.biaya)
    base_pel, base_lunak_norm, base_lunak, _ = ev.komponen(base)
    log(f"  populasi awal: {unik}/{a.populasi} individu unik, asal: {a.awal}; "
        f"terbaik: pelanggaran {base_pel}, lunak {base_lunak_norm:.3f}")

    rng = random.Random(seed + 7)
    ruang_gen = [list(range(len(ms.domain[k]))) for k in ev.kids]
    dom_n = [len(ms.domain[k]) for k in ev.kids]
    mulai = time.time()
    riwayat = []
    rata2 = []
    stat = dict(anak_diperbaiki=0, kelas_dipindah=0, sampel_n=0, sampel_sebelum=0, sampel_sesudah=0)   # bukti repair bekerja (dicuplik)
    rng_diag = random.Random(seed + 99)   # acak terpisah: diagnosis tidak mengubah jalannya GA
    konflik_pop = []   # rata-rata jumlah kelas bentrok pada populasi nyata di PyGAD tiap generasi (bukti repair tersimpan)

    def fitness(ga, sol, idx):
        return 1.0 / (1.0 + ev.biaya(sol))      # F = 1 / (1 + P), Bab 3

    def perbaiki(row):
        """[IMPROVISASI] repair: kelas yang terlibat bentrok H2-H4 dipindah ke penempatan bebas-bentrok.
        Gagasan repair pada GA penjadwalan: Mahlous & Mahlous 2023 (improveAllocations); manfaat repair mempersempit
        ruang pencarian: Rahimi dkk. 2023. Detail algoritma di bawah (gabung ke sesi mata kuliah yang sama dulu, lalu acak
        sampai coba_repair kali) adalah rancangan penulis kode."""
        jd = ev.dekode(row)
        konf = ms.kelas_konflik(jd)
        if not konf:
            return
        cuplik = rng_diag.random() < a.cuplik
        st = Keadaan(ms)
        sesi_mk = defaultdict(set)   # sesi (blok, ruangan) per mata kuliah dari kelas yang tidak bentrok
        for kid in ev.kids:
            if kid not in konf:
                st.tambah(kid, *jd[kid])
                sesi_mk[ms.kelas[kid]["mk"]].add(jd[kid])
        urut = sorted(konf)   # diurutkan dulu: urutan himpunan berubah antar proses (hash acak) dan merusak keterulangan seed
        rng.shuffle(urut)
        pos = {kid: i for i, kid in enumerate(ev.kids)}
        for kid in urut:
            i = pos[kid]
            mk = ms.kelas[kid]["mk"]
            if st.bisa(kid, *jd[kid]):
                st.tambah(kid, *jd[kid])
                sesi_mk[mk].add(jd[kid])
                continue
            # 1) gabung ke sesi mata kuliah yang sama bila bisa (penggabungan memang diperlukan agar kapasitas cukup)
            sesi = sorted(sesi_mk[mk])
            rng.shuffle(sesi)
            dipilih = None
            for o in sesi:
                if o in ms.domain_set[kid] and st.bisa(kid, *o):
                    dipilih = o
                    break
            # 2) penempatan acak dari domain, sampai coba_repair kali
            if dipilih is None:
                for _ in range(a.coba_repair):
                    o = ms.domain[kid][rng.randrange(dom_n[i])]
                    if st.bisa(kid, *o):
                        dipilih = o
                        break
            if dipilih is not None:
                st.tambah(kid, *dipilih)
                sesi_mk[mk].add(dipilih)
                row[i] = ev.indeks[kid][dipilih]
                stat["kelas_dipindah"] += 1
        if cuplik:   # diagnosis hanya pada sebagian kecil anak (kelas_konflik mahal)
            stat["sampel_n"] += 1
            stat["sampel_sebelum"] += len(konf)
            stat["sampel_sesudah"] += len(ms.kelas_konflik(ev.dekode(row)))

    def on_crossover(ga, offspring):
        # Bab 3 butir i: repair dipanggil setelah crossover dan mutation
        if a.repair:
            for row in offspring:
                if rng.random() < a.laju_repair:
                    stat["anak_diperbaiki"] += 1
                    perbaiki(row)

    def on_mutation(ga, offspring):
        if a.repair:
            for row in offspring:
                if rng.random() < a.laju_repair:
                    stat["anak_diperbaiki"] += 1
                    perbaiki(row)

    def on_generation(ga):
        terbaik = 1.0 / ga.best_solutions_fitness[-1] - 1.0   # kembali ke P
        riwayat.append(terbaik)
        fit = np.asarray(ga.last_generation_fitness, dtype=float)
        rata2.append(float(np.mean(1.0 / fit - 1.0)))
        g = ga.generations_completed
        if g == 1 or (a.log_diag and g % a.log_diag == 0):
            konflik_pop.append((g, float(np.mean([len(ms.kelas_konflik(ev.dekode(p))) for p in ga.population]))))
        if a.log and g % a.log == 0:
            log(f"  generasi {g}: biaya terbaik {terbaik:.4f}")
        if a.batas_menit and time.time() - mulai > a.batas_menit * 60:
            return "stop"

    orang_tua = a.orang_tua
    ga = pygad.GA(
        num_generations=a.generasi,
        num_parents_mating=orang_tua,
        fitness_func=fitness,
        initial_population=pop0,
        gene_type=int,
        gene_space=ruang_gen,                      
        parent_selection_type=a.seleksi,          
        K_tournament=a.k_turnamen,
        keep_elitism=a.elitisme,                   
        crossover_type=a.crossover,                 
        crossover_probability=a.p_crossover or None,  
        mutation_type="random",                     
        mutation_percent_genes=a.persen_mutasi,    
        on_crossover=on_crossover,
        on_mutation=on_mutation,
        on_generation=on_generation,
        stop_criteria=[f"saturate_{a.sabar}"] if a.sabar else None,
        random_seed=seed,
        suppress_warnings=True,
    )
    ga.run()
    sol, _, _ = ga.best_solution()
    detik = time.time() - mulai
    pel, lunak_norm, lunak, v = ev.komponen(sol)
    log(f"  repair: {stat['anak_diperbaiki']} anak diperiksa, {stat['kelas_dipindah']} kelas dipindah; "
        f"cuplikan {stat['sampel_n']} anak: bentrok {stat['sampel_sebelum']} -> {stat['sampel_sesudah']}")
    return dict(sol=sol, skala=dict(ev.skala), stat=stat, konflik_pop=konflik_pop, riwayat=riwayat, rata2=rata2, pop0=pop0, pel=pel, v=v, lunak=lunak, lunak_norm=lunak_norm,
                gen=ga.generations_completed, detik=detik, base=(base_pel, base_lunak_norm, base_lunak))


def analisis_ga(ms, ev, h, a, path):
    """Bukti apakah GA benar-benar mengubah solusi awal dari greedy: bandingkan individu terbaik populasi awal dengan
    solusi akhir (selisih biaya, jumlah gen yang berbeda, kapan perbaikan terakhir)."""
    pop0, sol = h["pop0"], h["sol"]
    awal = min(pop0, key=ev.biaya)
    _, ln_a, lk_a, v_a = ev.komponen(awal)
    _, ln_z, lk_z, v_z = ev.komponen(sol)
    jarak = [int(np.sum(p != sol)) for p in pop0]
    dekat = int(np.argmin(jarak))
    rw = h["riwayat"]
    perbaikan = [i + 1 for i in range(1, len(rw)) if rw[i] < rw[i - 1] - 1e-12]
    n = len(ev.kids)
    baris = [
        "ANALISIS GA: APAKAH GA MENGUBAH SOLUSI AWAL?",
        f"Asal populasi awal: {a.awal}; ukuran populasi {len(pop0)}; jumlah gen (kelas) {n}",
        f"Generasi yang dijalankan: {h['gen']} dari maksimum {a.generasi}; waktu {h['detik']:.0f} detik",
        "",
        "1) Biaya P (makin kecil makin baik)",
        f"   individu awal terbaik : {ev.biaya(awal):.6f}  (pelanggaran keras {sum(v_a.values())})",
        f"   solusi akhir GA       : {ev.biaya(sol):.6f}  (pelanggaran keras {sum(v_z.values())})",
        f"   perbaikan             : {100 * (1 - ev.biaya(sol) / ev.biaya(awal)) if ev.biaya(awal) else 0:.2f}%",
        "",
        "2) Komponen lunak mentah: awal -> akhir",
    ] + [f"   {k:10s}: {lk_a[k]} -> {lk_z[k]}" for k in [n for n, _ in SUKU] + ["f_berbobot"]] + [
        "",
        "3) Seberapa berbeda solusi akhir dari populasi awal",
        f"   gen berbeda dari individu awal terbaik : {int(np.sum(awal != sol))} dari {n}",
        f"   individu awal TERDEKAT: nomor {dekat}, berbeda {jarak[dekat]} gen ({100 * jarak[dekat] / n:.1f}%)",
        f"   solusi akhir identik dengan individu awal: {'YA' if jarak[dekat] == 0 else 'TIDAK'}",
        "",
        "4) Perjalanan pencarian",
        f"   jumlah generasi yang memperbaiki biaya terbaik : {len(perbaikan)}",
        f"   generasi perbaikan terakhir                   : {perbaikan[-1] if perbaikan else 'tidak ada (biaya terbaik tetap sejak generasi 1)'}",
        f"   biaya terbaik generasi 1 -> terakhir          : {rw[0]:.6f} -> {rw[-1]:.6f}",
        f"   biaya rata-rata populasi generasi 1 -> terakhir: {h['rata2'][0]:.4f} -> {h['rata2'][-1]:.4f}",
        "",
        "5) Bukti kerja repair (hanya bila repair aktif)",
        (f"   cuplikan {h['stat']['sampel_n']} anak dari {h['stat']['anak_diperbaiki']} yang diperbaiki: kelas bentrok sebelum repair "
         f"{h['stat']['sampel_sebelum']}, sesudah repair {h['stat']['sampel_sesudah']}; kelas dipindah (seluruhnya) {h['stat']['kelas_dipindah']}"
         if a.repair else "   repair dimatikan (--tanpa-repair)"),
        "   rata-rata kelas bentrok pada populasi nyata PyGAD (dicuplik): " + ", ".join(f"gen {g} = {v:.1f}" for g, v in h['konflik_pop']),
        "   (bila 'sesudah repair' kecil tetapi populasi nyata tetap tinggi, perubahan repair tidak tersimpan oleh PyGAD)",
        "",
        "Cara membaca:",
        "  - Elitisme menjamin solusi terbaik tidak pernah memburuk, jadi biaya akhir <= biaya individu awal terbaik.",
        "  - Jika 'perbaikan' 0% dan 'gen berbeda' 0, GA tidak menemukan jadwal yang lebih baik dari populasi awal.",
        "  - Jika rata-rata populasi membaik tetapi terbaik tetap, GA bekerja tetapi belum melampaui solusi awal terbaik.",
        "  - Jika rata-rata populasi memburuk/melonjak, operator (mis. mutasi) terlalu merusak.",
    ]
    Path(path).write_text("\n".join(baris) + "\n", encoding="utf-8")
    return baris


_MS_CACHE = {}


def _kerja(arg):
    """Pekerja untuk --proses > 1: tiap proses membangun model sekali lalu menjalankan satu run."""
    folder, a, seed = arg
    if folder not in _MS_CACHE:
        _MS_CACHE[folder] = Masalah(Path(folder))
    h = jalankan(_MS_CACHE[folder], a, seed, lambda m: print(f"[seed {seed}]{m}", flush=True))
    return h


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="GA penjadwalan (PyGAD)")
    ap.add_argument("--bersih", default=str(base / "bersih"))
    ap.add_argument("--hasil", help="folder run pembersihan, mis. hasil2 (bawaan: terbaru)")
    ap.add_argument("--keluar", default=str(base / "ga"))
    ap.add_argument("--populasi", type=int, default=20)            # Tabel 7
    ap.add_argument("--generasi", type=int, default=3000)          # Tabel 7
    ap.add_argument("--orang-tua", type=int, default=10, help="num_parents_mating (Tabel 7: 10)")
    ap.add_argument("--awal", default="greedy", choices=["greedy", "acak"], help="populasi awal: greedy (Bab 3) atau acak (pembanding)")
    ap.add_argument("--kandidat", type=int, default=30, help="jumlah kandidat per kelas pada greedy populasi awal")
    ap.add_argument("--seleksi", default="sss", choices=["sss", "tournament", "rws", "rank", "random"])
    ap.add_argument("--k-turnamen", type=int, default=3)           # hanya bila --seleksi tournament
    ap.add_argument("--elitisme", type=int, default=1)             # Tabel 7
    ap.add_argument("--crossover", default="single_point", choices=["single_point", "two_points", "uniform"])
    ap.add_argument("--p-crossover", type=float, default=0.0, help="0 = bawaan PyGAD (selalu crossover)")
    ap.add_argument("--persen-mutasi", type=float, default=10.0)   # Tabel 7: 10% gen
    ap.add_argument("--tanpa-repair", dest="repair", action="store_false")
    ap.add_argument("--laju-repair", type=float, default=1.0, help="peluang tiap anak diperbaiki (Bab 3: semua anak)")
    ap.add_argument("--coba-repair", type=int, default=40)         # [IMPROVISASI] detail algoritma repair
    ap.add_argument("--sabar", type=int, default=200, help="saturate_N (Tabel 7: 200; 0 = nonaktif)")
    ap.add_argument("--M", type=float, default=1000.0, help="bobot dominan hard constraint (Tabel 7: 1000)")
    ap.add_argument("--ulangan", type=int, default=30, help="jumlah run independen (Bab 3: 30; untuk uji cepat pakai 1)")
    ap.add_argument("--seed", type=int, default=5000, help="seed run pertama; run ke-i memakai seed+i (Bab 3: 5000-5029)")
    ap.add_argument("--log", type=int, default=25)
    ap.add_argument("--batas-menit", type=float, default=0)
    ap.add_argument("--cuplik", type=float, default=0.05, help="bagian anak yang dicek ulang setelah repair untuk diagnosis (0 = mati)")
    ap.add_argument("--log-diag", type=int, default=50, help="tiap N generasi hitung kelas bentrok pada populasi (0 = hanya generasi 1)")
    ap.add_argument("--proses", type=int, default=1, help="jumlah run yang dijalankan paralel (satu proses per run)")
    ap.add_argument("--label", default="", help="nama konfigurasi untuk perbandingan, mis. bab3 / mutasi1")
    ap.add_argument("--tanpa-simpan-per-run", dest="simpan_per_run", action="store_false",
                    help="jangan simpan jadwal awal dan akhir tiap run (bawaan: disimpan di jadwal_per_run/, dipakai bandingkan_jadwal.py)")
    ap.set_defaults(repair=True, simpan_per_run=True)
    a = ap.parse_args()

    bersih = Path(a.bersih)
    if a.hasil:
        folder = bersih / a.hasil
    else:
        cand = [p for p in bersih.glob("hasil*") if re.fullmatch(r"hasil\d+", p.name) and (p / f"status{p.name[5:]}.txt").exists()]
        if not cand:
            sys.exit(f"Tidak ada folder hasilN di {bersih}. Jalankan pembersihan data dulu.")
        folder = max(cand, key=lambda p: int(p.name[5:]))
    ms = Masalah(folder)
    if any(not d for d in ms.domain.values()):
        sys.exit("Ada kelas dengan domain kosong; jalankan src/pemodelan.py untuk detailnya.")

    keluar = Path(a.keluar)
    keluar.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(keluar, "ga")
    out = keluar / f"ga{N}"
    out.mkdir()
    print(f"Memakai {folder.name}: {len(ms.kelas)} kelas. Keluaran: {out}")

    nama_suku = [n for n, _ in SUKU]
    kepala = (["seed", "pelanggaran_keras", "lunak_ternormalisasi"] + nama_suku +
              ["f_berbobot_mentah", "generasi", "detik", "biaya_awal_terbaik", "biaya_akhir", "gen_berbeda_dari_awal",
               "awal_pelanggaran_keras"] + [f"awal_{n}" for n in nama_suku] + ["awal_f_berbobot", "skor_bersama", "skor_bersama_awal"])
    seeds = [a.seed + u for u in range(a.ulangan)]
    sementara = out / f"ringkasan_sementara{N}.csv"   # ditulis tiap satu run selesai: bila proses terhenti, hasil run yang selesai tetap ada
    tulis(sementara, ["seed", "pelanggaran_keras", "f_berbobot", "generasi", "detik"], [])

    def catat(sd, h):
        with open(sementara, "a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow([sd, h["pel"], h["lunak"]["f_berbobot"], h["gen"], round(h["detik"], 1)])

    semua = []
    if a.proses > 1 and a.ulangan > 1:
        import multiprocessing as mp
        print(f"{a.ulangan} run dijalankan paralel dengan {a.proses} proses", flush=True)
        try:
            with mp.get_context("spawn").Pool(a.proses) as kolam:
                for sd, h in zip(seeds, kolam.imap(_kerja, [(str(folder), a, sd) for sd in seeds], chunksize=1)):
                    semua.append(h)
                    catat(sd, h)
                    print(f"  [selesai {len(semua)}/{a.ulangan}] seed {sd}: pelanggaran {h['pel']}, {h['gen']} generasi, {h['detik']:.0f} detik", flush=True)
        except Exception as e:   # mis. masalah multiprocessing di Windows: lanjutkan berurutan, jangan gagal total
            print(f"PERINGATAN: mode paralel gagal ({type(e).__name__}: {e}); sisa run dijalankan berurutan.", flush=True)
    for u in range(len(semua), len(seeds)):
        sd = seeds[u]
        print(f"Run {u + 1}/{a.ulangan} (seed {sd})", flush=True)
        h = jalankan(ms, a, sd, lambda m: print(m, flush=True))
        semua.append(h)
        catat(sd, h)
    ringkas, terbaik = [], None
    import statistics as stt
    # [PERBAIKAN pemilihan] Skor lunak ternormalisasi memakai skala tiap run (rata-rata populasi awal run itu), jadi TIDAK sebanding
    # antar run. Run terbaik dipilih memakai skor bersama: skala yang sama untuk semua run (rata-rata skala semua run).
    skala_b = {n: max(1.0, stt.mean(h["skala"][n] for h in semua)) for n in nama_suku}
    skor_bersama = lambda lun: sum(BOBOT[k] * lun[n] / skala_b[n] for n, k in SUKU)
    if a.simpan_per_run:
        (out / "jadwal_per_run").mkdir(exist_ok=True)
    for sd, hasil in zip(seeds, semua):
        ev_h = Evaluasi(ms, a.M)
        ev_h.skala = hasil["skala"]
        hasil["ev"] = ev_h
        awal_ind = min(hasil["pop0"], key=ev_h.biaya)
        awal_b, akhir_b = ev_h.biaya(awal_ind), ev_h.biaya(hasil["sol"])
        gen_beda = int(np.sum(awal_ind != hasil["sol"]))
        ringkas.append([sd, hasil["pel"], round(hasil["lunak_norm"], 4), *[hasil["lunak"][n] for n in nama_suku],
                        hasil["lunak"]["f_berbobot"], hasil["gen"], round(hasil["detik"], 1), round(awal_b, 6), round(akhir_b, 6), gen_beda,
                        hasil["base"][0]] + [hasil["base"][2][n] for n in nama_suku] + [hasil["base"][2]["f_berbobot"],
                        round(skor_bersama(hasil["lunak"]), 4), round(skor_bersama(hasil["base"][2]), 4)])
        if a.simpan_per_run:
            tulis_jadwal(ms, ev_h.dekode(awal_ind), out / "jadwal_per_run" / f"awal_seed{sd}.csv")
            tulis_jadwal(ms, ev_h.dekode(hasil["sol"]), out / "jadwal_per_run" / f"akhir_seed{sd}.csv")
        print(f"  seed {sd}: pelanggaran keras {hasil['pel']}, lunak ternormalisasi {hasil['lunak_norm']:.4f}, "
              f"{hasil['gen']} generasi, {hasil['detik']:.0f} detik, gen berbeda dari awal {gen_beda}")
        kunci = (hasil["pel"], skor_bersama(hasil["lunak"]))
        if terbaik is None or kunci < (terbaik["pel"], skor_bersama(terbaik["lunak"])):
            terbaik = hasil

    tulis(out / f"ringkasan_ulangan{N}.csv", kepala, ringkas)
    # statistik run (Bab 3: terbaik, rata-rata, simpangan baku)
    k = {nm: i for i, nm in enumerate(kepala)}
    def st(nm):
        xs = [r[k[nm]] for r in ringkas]
        return f"min {min(xs):.4g}, rata-rata {stt.mean(xs):.4g}, simpangan baku {stt.pstdev(xs):.4g}, maks {max(xs):.4g}"
    tulis_stat = [
        f"STATISTIK {a.ulangan} RUN GA [{a.label or 'tanpa label'}] (seed {a.seed}..{a.seed + a.ulangan - 1})",
        f"Run sah (0 pelanggaran keras): {sum(1 for r in ringkas if r[k['pelanggaran_keras']] == 0)}/{a.ulangan}",
        "Run terbaik (jadwal_ga) dipilih dengan skor bersama (skala sama untuk semua run), bukan skor ternormalisasi per run.",
        f"Pelanggaran keras   : {st('pelanggaran_keras')}",
        f"Lunak ternormalisasi: {st('lunak_ternormalisasi')}",
        f"f_berbobot mentah   : {st('f_berbobot_mentah')}",
    ] + [f"{nm:20s}: {st(nm)}" for nm in nama_suku] + [
        f"Generasi            : {st('generasi')}",
        f"Waktu (detik)       : {st('detik')}",
        f"Biaya awal terbaik  : {st('biaya_awal_terbaik')}",
        f"Biaya akhir         : {st('biaya_akhir')}",
        f"Run yang BERUBAH dari solusi awal (gen berbeda > 0): {sum(1 for r in ringkas if r[k['gen_berbeda_dari_awal']] > 0)}/{a.ulangan}",
        f"Run yang MEMBAIK dari solusi awal (biaya akhir < biaya awal): "
        f"{sum(1 for r in ringkas if r[k['biaya_akhir']] < r[k['biaya_awal_terbaik']] - 1e-12)}/{a.ulangan}",
    ]
    (out / f"statistik_ulangan{N}.txt").write_text("\n".join(tulis_stat) + "\n", encoding="utf-8")
    print("\n".join(tulis_stat))
    jd = terbaik["ev"].dekode(terbaik["sol"])
    tulis_jadwal(ms, jd, out / f"jadwal_ga{N}.csv")
    v, lunak, rinci = ms.validasi(jd)  # pemeriksaan akhir oleh validator lengkap (sumber kebenaran)
    tulis(out / f"laporan_validasi{N}.csv", ["indikator", "nilai"], list(v.items()) + list(lunak.items()))
    tulis(out / f"rincian_pelanggaran{N}.csv", ["jenis", "keterangan"], [[j, t] for j, lst in rinci.items() for t in lst])
    rw, rt = terbaik["riwayat"], terbaik["rata2"]
    tulis(out / f"riwayat_biaya{N}.csv", ["generasi", "biaya_terbaik", "biaya_rata2_populasi"],
          [[i + 1, round(b, 6), round(rt[i], 6)] for i, b in enumerate(rw)])
    ev = terbaik["ev"]
    analisis_ga(ms, ev, terbaik, a, out / f"analisis_ga{N}.txt")
    param = [["populasi", a.populasi], ["generasi_maks", a.generasi], ["orang_tua", a.orang_tua],
             ["seleksi", a.seleksi], ["k_turnamen", a.k_turnamen], ["elitisme", a.elitisme], ["crossover", a.crossover],
             ["p_crossover", a.p_crossover or "bawaan PyGAD"], ["persen_mutasi", a.persen_mutasi], ["repair", a.repair], ["laju_repair", a.laju_repair],
             ["coba_repair", a.coba_repair], ["sabar", a.sabar], ["M", ev.M], ["bobot", BOBOT]] + \
            [[f"skala_{k}", round(s, 4)] for k, s in ev.skala.items()] + \
            [["label", a.label], ["awal", a.awal], ["terbaik_populasi_awal_pelanggaran", terbaik["base"][0]], ["terbaik_populasi_awal_lunak_norm", round(terbaik["base"][1], 4)]]
    tulis(out / f"parameter{N}.csv", ["parameter", "nilai"], param)
    buat_laporan(ms, jd, out / "laporan")
    sah = sum(v.values()) == 0
    with open(out / f"status{N}.txt", "w", encoding="utf-8") as f:
        f.write(f"JADWAL: {N}\nSUMBER: {folder.name}\nPELANGGARAN_KERAS: {sum(v.values())}\nSAH: {'YA' if sah else 'TIDAK'}\n"
                f"METODE: ga (awal={a.awal})\nPEMILIHAN: skor_bersama\nLABEL: {a.label}\nLUNAK_TERNORMALISASI: {terbaik['lunak_norm']:.4f}\nULANGAN: {a.ulangan}\n")
    print(f"Terbaik: pelanggaran keras {sum(v.values())} ({'SAH' if sah else 'TIDAK SAH'}), {lunak}")
    print(f"Hasil di {out}")
    return 0 if sah else 1


if __name__ == "__main__":
    sys.exit(main())