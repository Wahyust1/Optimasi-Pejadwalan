"""
Tahap 3a - Heuristik greedy (konstruktif) untuk penjadwalan kuliah.

Berdiri sendiri, terpisah dari GA (ga.py). Keduanya memakai model yang sama (pemodelan.py: domain,
validator H1-H6, hitungan S1-S4) sehingga hasilnya dapat dibandingkan secara adil pada metrik yang sama.

Cara kerja (Bab 3 butir b): kelas berdomain terkecil diprioritaskan; tiap kelas diberi penempatan yang memenuhi
hard constraint dengan tambahan penalti soft constraint terkecil. Kelas yang tidak dapat ditempatkan dicatat
sebagai "gagal" (jadwal parsial). Greedy dijalankan berulang dengan seed berbeda (derau acak pada urutan dan
pencuplikan kandidat), dan hasil terbaik dilaporkan beserta sebarannya. Pilihan --kriteria heuristik tersedia sebagai pembanding.

Contoh:
  python src/greedy.py                      # 30 percobaan (seed 1..30), laporkan yang terbaik + sebaran
  python src/greedy.py --percobaan 100
  python src/greedy.py --hasil hasil2

Keluaran: data/greedy/greedyN/ (nomor bertambah, tidak menimpa; terpisah dari data/ga/)
  jadwal_greedyN.csv, laporan_validasiN.csv, rincian_pelanggaranN.csv, kelas_gagalN.csv,
  ringkasan_percobaanN.csv (tiap seed), analisis_greedyN.txt, statusN.txt,
  laporan/ (jadwal per prodi, dosen, hari, ruangan, paket; juga laporan_jadwal.xlsx)
"""
import argparse
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from laporan import buat_laporan
from pemodelan import BOBOT, SUKU, Keadaan, Masalah, nomor_berikut, tulis, tulis_jadwal


def bangun(ms, rng, kriteria="heuristik", kandidat=30, gabung_dulu=True):
    """Satu percobaan greedy acak. Mengembalikan (jadwal parsial {kelas: (blok, ruangan)}, daftar kelas gagal).

    Urutan: kelas berdomain terkecil lebih dulu (Bab 3 butir b), dengan derau acak kecil agar tiap seed berbeda.
    kriteria 'penalti' (Bab 3; BUKAN bawaan karena pada data uji gagal menjadwalkan 18-47 kelas): tiap kelas ditempatkan pada alternatif yang memenuhi H2-H4 dengan
      TAMBAHAN penalti lunak terkecil. Alternatif yang diperiksa: semua sesi mata kuliah yang sama yang sudah ada
      + (hanya bila tidak ada sesi yang bisa dimasuki) sampai `kandidat` alternatif acak lain ([IMPROVISASI]: pencuplikan demi kecepatan; memeriksa seluruh domain
      ~280 alternatif per kelas terlalu lambat untuk membangun puluhan individu).
    gabung_dulu=True [IMPROVISASI]: bila ada sesi mata kuliah yang sama yang bisa dimasuki, kandidat dibatasi pada sesi itu.
      Alasan: penalti S1 melarang penggabungan, padahal kapasitas hanya cukup bila banyak kelas digabung (uji: tanpa
      aturan ini 0/10 percobaan layak, 34-47 kelas gagal). Dengan aturan ini penalti hanya memilih di antara sesi tsb.
    kriteria 'heuristik' (bawaan): gabung dulu ke sesi yang ada, bila tidak sesi baru (ruangan besar dulu untuk mata kuliah
      banyak paralel, kecil dulu untuk mata kuliah tunggal). [IMPROVISASI] pembanding."""
    st = Keadaan(ms)
    sesi_mk = defaultdict(list)  # mk -> [(blok_awal, ruangan)]
    jadwal, gagal = {}, []
    paralel = defaultdict(int)
    for k in ms.kelas.values():
        paralel[k["mk"]] += 1
    urut = sorted(ms.kelas, key=lambda kid: len(ms.domain[kid]) * (0.7 + 0.6 * rng.random()))
    w = BOBOT
    nilai = lambda jd: ms.lunak(jd)["f_berbobot"]
    sekarang = 0.0
    for kid in urut:
        mk = ms.kelas[kid]["mk"]
        pilih = None
        sesi = list(sesi_mk[mk])
        rng.shuffle(sesi)
        if kriteria == "penalti":
            cand = [(b, r) for (b, r) in sesi if (b, r) in ms.domain_set[kid] and st.bisa(kid, b, r)]
            if not (gabung_dulu and cand):
                ada = set(cand)
                opsi = list(ms.domain[kid])
                rng.shuffle(opsi)
                n_lain = 0
                for o in opsi:
                    if n_lain >= kandidat:
                        break
                    if o not in ada and st.bisa(kid, *o):
                        cand.append(o)
                        n_lain += 1
            if cand:
                terbaik = None
                for o in cand:
                    jadwal[kid] = o
                    nv = nilai(jadwal)
                    del jadwal[kid]
                    if terbaik is None or nv < terbaik[0]:
                        terbaik = (nv, o)
                pilih = terbaik[1]
                sekarang = terbaik[0]
                if pilih not in sesi_mk[mk]:
                    sesi_mk[mk].append(pilih)
        else:
            for (b, r) in sesi:
                if (b, r) in ms.domain_set[kid] and st.bisa(kid, b, r):
                    pilih = (b, r)
                    break
            if pilih is None:
                opsi = list(ms.domain[kid])
                rng.shuffle(opsi)
                opsi.sort(key=lambda o: -ms.ruang[o[1]]["cap"] if paralel[mk] > 1 else ms.ruang[o[1]]["cap"])
                for (b, r) in opsi:
                    if st.bisa(kid, b, r):
                        pilih = (b, r)
                        sesi_mk[mk].append((b, r))
                        break
        if pilih is None:
            gagal.append(kid)
            continue
        st.tambah(kid, *pilih)
        jadwal[kid] = pilih
    return jadwal, gagal


def pilih_folder(bersih, hasil):
    if hasil:
        return bersih / hasil
    cand = [p for p in bersih.glob("hasil*") if re.fullmatch(r"hasil\d+", p.name) and (p / f"status{p.name[5:]}.txt").exists()]
    if not cand:
        sys.exit(f"Tidak ada folder hasilN di {bersih}. Jalankan pembersihan data dulu.")
    return max(cand, key=lambda p: int(p.name[5:]))


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Greedy konstruktif untuk penjadwalan kuliah")
    ap.add_argument("--bersih", default=str(base / "bersih"))
    ap.add_argument("--hasil", help="folder run pembersihan, mis. hasil2 (bawaan: terbaru)")
    ap.add_argument("--keluar", default=str(base / "greedy"))
    ap.add_argument("--percobaan", type=int, default=30, help="jumlah percobaan (seed berurutan)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--simpan-semua", default="otomatis", choices=["otomatis", "ya", "tidak"],
                    help="simpan jadwal tiap percobaan ke jadwal_per_percobaan/ (otomatis: bila percobaan <= 200); dipakai bandingkan_jadwal.py")
    ap.add_argument("--kriteria", default="heuristik", choices=["penalti", "heuristik"],
                    help="penalti: tambahan penalti lunak terkecil (Bab 3, tidak layak pada data uji); heuristik (bawaan): gabung dulu + ukuran ruangan")
    ap.add_argument("--kandidat", type=int, default=30, help="alternatif acak yang diperiksa per kelas (kriteria penalti)")
    a = ap.parse_args()

    folder = pilih_folder(Path(a.bersih), a.hasil)
    ms = Masalah(folder)
    kosong = [kid for kid, d in ms.domain.items() if not d]
    if kosong:
        sys.exit(f"{len(kosong)} kelas berdomain kosong; jalankan src/pemodelan.py untuk detailnya.")
    keluar = Path(a.keluar)
    keluar.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(keluar, "greedy")
    out = keluar / f"greedy{N}"
    out.mkdir()
    n = len(ms.kelas)
    print(f"Memakai {folder.name}: {n} kelas. Keluaran: {out}")

    hasil, mentah = [], []
    for i in range(a.percobaan):
        seed = a.seed + i
        t0 = time.time()
        jd, gagal = bangun(ms, random.Random(seed), a.kriteria, a.kandidat)
        detik = time.time() - t0
        v, lunak, _ = ms.validasi(jd, detail=False)
        pel = sum(v.values())
        hasil.append([seed, n - len(gagal), len(gagal), pel] + [lunak[nm] for nm, _ in SUKU] + [lunak["f_berbobot"], round(detik, 3)])
        mentah.append((seed, jd, gagal, pel, lunak))
    # [PERBAIKAN pemilihan] f_berbobot mentah didominasi S3 (nilainya ribuan), jadi memilih "terbaik" dengannya hampir sama dengan memilih
    # S3 terkecil. Dipakai skor bersama: tiap suku dibagi rata-ratanya pada percobaan lengkap-sah, lalu dibobot (sama dengan f_ref).
    lengkap_m = [m for m in mentah if not m[2] and m[3] == 0]
    skala_g = {nm: max(1.0, statistics.mean(m[4][nm] for m in lengkap_m)) if lengkap_m else 1.0 for nm, _ in SUKU}
    skor = lambda lun: sum(BOBOT[k] * lun[nm] / skala_g[nm] for nm, k in SUKU)
    for h, m in zip(hasil, mentah):
        h.append(round(skor(m[4]), 4))
    terbaik = None
    for seed, jd, gagal, pel, lunak in mentah:
        kunci = (len(gagal), pel, skor(lunak))   # sedikit kelas gagal dulu, lalu pelanggaran, lalu skor bersama
        if terbaik is None or kunci < terbaik[0]:
            terbaik = (kunci, seed, jd, gagal)
    if a.simpan_semua == "ya" or (a.simpan_semua == "otomatis" and a.percobaan <= 200):
        pp = out / "jadwal_per_percobaan"
        pp.mkdir()
        for seed, jd, gagal, pel, lunak in mentah:
            tulis_jadwal(ms, jd, pp / f"seed{seed}.csv")
    tulis(out / f"ringkasan_percobaan{N}.csv",
          ["seed", "kelas_terjadwal", "kelas_gagal", "pelanggaran_keras"] + [nm for nm, _ in SUKU] + ["f_berbobot", "detik", "skor_bersama"], hasil)

    _, seed_b, jd, gagal = terbaik
    v, lunak, rinci = ms.validasi(jd)
    tulis_jadwal(ms, jd, out / f"jadwal_greedy{N}.csv")
    tulis(out / f"laporan_validasi{N}.csv", ["indikator", "nilai"],
          [["kelas_terjadwal", len(jd)], ["kelas_gagal", len(gagal)]] + list(v.items()) + list(lunak.items()))
    tulis(out / f"kelas_gagal{N}.csv", ["kelas_id", "nama_kelas", "ukuran_domain"],
          [[kid, ms.kelas[kid]["nama"], len(ms.domain[kid])] for kid in gagal])
    tulis(out / f"rincian_pelanggaran{N}.csv", ["jenis", "keterangan"], [[j, t] for j, lst in rinci.items() for t in lst])

    # ---- analisis sebaran percobaan
    lengkap = [h for h in hasil if h[2] == 0 and h[3] == 0]
    kol = {nm: 4 + i for i, (nm, _) in enumerate(SUKU)}
    kol["f_berbobot"] = 4 + len(SUKU)
    kol["detik"] = 5 + len(SUKU)
    def ringkas(nm):
        xs = [h[kol[nm]] for h in lengkap]
        return (f"min {min(xs):.1f}, rata-rata {statistics.mean(xs):.1f}, simpangan baku {statistics.pstdev(xs):.1f}, maks {max(xs):.1f}" if xs else "-")
    gagal_semua = [h[2] for h in hasil]
    baris = [
        "ANALISIS GREEDY",
        f"Sumber data: {folder.name} ({n} kelas)",
        f"Kriteria: {a.kriteria}, kandidat {a.kandidat}",
        "Percobaan terbaik dipilih dengan skor bersama (tiap suku dibagi rata-ratanya pada percobaan sah, lalu dibobot), bukan f_berbobot mentah.",
        f"Percobaan: {a.percobaan} (seed {a.seed}..{a.seed + a.percobaan - 1})",
        f"Percobaan yang menjadwalkan SEMUA kelas tanpa pelanggaran keras: {len(lengkap)}/{a.percobaan} "
        f"({100 * len(lengkap) / a.percobaan:.0f}%)",
        f"Kelas gagal per percobaan: min {min(gagal_semua)}, rata-rata {statistics.mean(gagal_semua):.1f}, maks {max(gagal_semua)}",
        "Sebaran pada percobaan yang lengkap dan sah:",
    ] + [f"  {nm:13s}: {ringkas(nm)}" for nm, _ in SUKU] + [
        f"  {'f_berbobot':13s}: {ringkas('f_berbobot')}",
        f"Waktu per percobaan: rata-rata {statistics.mean(h[kol['detik']] for h in hasil):.2f} detik",
        f"Percobaan terbaik: seed {seed_b}: terjadwal {len(jd)}/{n}, gagal {len(gagal)}, pelanggaran keras {sum(v.values())}",
        f"Batasan lunak terbaik: {lunak}",
        f"Bobot: {BOBOT}",
    ]
    (out / f"analisis_greedy{N}.txt").write_text("\n".join(baris) + "\n", encoding="utf-8")
    sah = not gagal and sum(v.values()) == 0
    with open(out / f"status{N}.txt", "w", encoding="utf-8") as f:
        f.write(f"JADWAL: {N}\nMETODE: greedy\nSUMBER: {folder.name}\nKELAS_TERJADWAL: {len(jd)}\nKELAS_GAGAL: {len(gagal)}\n"
                f"PELANGGARAN_KERAS: {sum(v.values())}\nSAH: {'YA' if sah else 'TIDAK'}\n"
                f"KRITERIA: {a.kriteria}\nPERCOBAAN: {a.percobaan}\n")
    buat_laporan(ms, jd, out / "laporan")
    print("\n".join(baris))
    print(f"Hasil di {out}")
    return 0 if sah else 1


if __name__ == "__main__":
    sys.exit(main())