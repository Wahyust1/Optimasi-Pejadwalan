"""
cuplik_pemodelan.py - Cuplikan hasil pemodelan untuk laporan (Subbab 4.2): data kelas, domain satu kelas, rekap ukuran domain.

Hanya MEMBACA data bersih; keluaran ke data\\pemeriksaan\\domainN (tidak menimpa).
Contoh:
  python src\\cuplik_pemodelan.py --hasil data\\bersih\\hasil5 --kelas "PSTE:kalkulus-2:A"
Keluaran: layar (untuk tangkapan layar Gambar 4.4 dan 4.5 dan 4.6) dan berkas di data\\pemeriksaan\\domainN:
  cuplikan_kelas.csv, domain_kelas.csv, rekap_domain.csv, ringkasan_domain.txt
"""
import argparse
import csv
import statistics as st
import sys
from collections import Counter
from pathlib import Path

from pemodelan import Masalah


def tulis(p, kepala, baris):
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(kepala)
        w.writerows(baris)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hasil", required=True, help="folder data bersih, mis. data\\bersih\\hasil5")
    ap.add_argument("--kelas", default="PSTE:kalkulus-2:A", help="kelas_id yang domainnya ditampilkan")
    ap.add_argument("--jumlah", type=int, default=6, help="jumlah kelas pada cuplikan data")
    ap.add_argument("--keluar", default="data/pemeriksaan")
    a = ap.parse_args()
    ms = Masalah(a.hasil)
    if a.kelas not in ms.kelas:
        sys.exit(f"kelas_id tidak ada: {a.kelas}")
    N = 1
    while (Path(a.keluar) / f"domain{N}").exists():
        N += 1
    out = Path(a.keluar) / f"domain{N}"
    out.mkdir(parents=True)
    L = []

    def p(s=""):
        print(s)
        L.append(s)

    sz = {k: len(d) for k, d in ms.domain.items()}
    # --- Gambar 4.4: cuplikan data kelas
    p("=== CUPLIKAN DATA HASIL PEMODELAN (kelas, SKS, dosen, prodi, paket) ===")
    kep = ["kelas_id", "prodi", "jenis", "sks_blok", "dosen", "paket"]
    baris = []
    ids = sorted(ms.kelas)
    langkah = max(1, len(ids) // a.jumlah)
    for kid in ids[::langkah][:a.jumlah]:
        k = ms.kelas[kid]
        dos = "; ".join(sorted(ms.nama_dosen.get(d, d) for d in ms.kelas_dosen[kid]))
        baris.append([kid, k["prodi"], k["jenis"], k["sks"], dos, ",".join(sorted(ms.kelas_paket[kid])) or "-"])
    for b in baris:
        p(" | ".join(str(x) for x in b))
    tulis(out / "cuplikan_kelas.csv", kep, baris)

    # --- Gambar 4.5: domain satu kelas
    kid = a.kelas
    d = ms.domain[kid]
    k = ms.kelas[kid]
    p("")
    p(f"=== DOMAIN PENEMPATAN {kid} ({k['prodi']}, {k['jenis']}, {k['sks']} blok): {len(d)} alternatif ===")
    rows = []
    for i, (b, r) in enumerate(d, 1):
        s = ms.urut[(kid, b)]
        rows.append([i, ms.blok[b]["hari"], b, f"{ms.blok[s[0]]['mulai']}-{ms.blok[s[-1]]['selesai']}", ms.ruang[r]["nama"]])
    for i in list(range(3)) + [len(rows) - 1]:
        p(" | ".join(str(x) for x in rows[i]))
    p(f"blok awal berbeda: {len({b for b, r in d})}, ruangan berbeda: {len({r for b, r in d})}")
    p("alternatif per hari: " + ", ".join(f"{h} {n}" for h, n in Counter(ms.blok[b]['hari'] for b, r in d).items()))
    tulis(out / "domain_kelas.csv", ["no", "hari", "blok_awal", "jam", "ruangan"], rows)

    # --- Gambar 4.6: rekap domain
    v = sorted(sz.values())
    p("")
    p("=== REKAP PEMBENTUKAN DOMAIN ===")
    p(f"kelas dimodelkan: {len(v)}; total alternatif: {sum(v)}; domain kosong: {sum(1 for x in v if x == 0)}; tidak kosong: {sum(1 for x in v if x > 0)}")
    p(f"minimum {v[0]}; median {st.median(v):g}; maksimum {v[-1]}; rata-rata {st.mean(v):.2f}")
    rek = []
    for pr in ("PSTE", "PSTI", "MTE"):
        w = [sz[x] for x, c in ms.kelas.items() if c["prodi"].upper() == pr]
        if w:
            rek.append([f"prodi {pr}", len(w), min(w), st.median(w), max(w), round(st.mean(w), 1)])
    for s in sorted({c["sks"] for c in ms.kelas.values()}):
        w = [sz[x] for x, c in ms.kelas.items() if c["sks"] == s]
        rek.append([f"durasi {s} blok", len(w), min(w), st.median(w), max(w), round(st.mean(w), 1)])
    for r in rek:
        p(" | ".join(str(x) for x in r))
    tulis(out / "rekap_domain.csv", ["kelompok", "kelas", "min", "median", "maks", "rata2"], rek)
    (out / "ringkasan_domain.txt").write_text("\n".join(L), encoding="utf-8")
    p(f"\nHasil di {out}")


if __name__ == "__main__":
    main()