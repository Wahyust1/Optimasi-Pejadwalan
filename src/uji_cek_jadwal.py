"""
uji_cek_jadwal.py - Pengujian cek_jadwal.py (bahan Bab IV 4.9).

Menguji bahwa jawaban cek_jadwal.py benar, dengan membandingkannya dengan hitungan INDEPENDEN dari berkas CSV jadwal dan data bersih
(tanpa memakai fungsi pengecekan cek_jadwal.py), serta dengan sabotase sengaja:

  C1  Jadwal sah: ringkasan dosen, ruangan, dan paket semuanya "OK" (tanpa bentrok), sepakat dengan audit (0 pelanggaran),
      dan jumlah entri sesuai data (dosen mengampu, ruangan, paket).
  C2  Jumlah kelas tiap dosen pada ringkasan = hitungan dari kelas_dosen.csv dan jadwal (semua dosen).
  C3  Total blok mengajar tiap dosen pada ringkasan = hitungan dari jam jadwal (durasi // 50 menit, satu sesi gabungan dihitung sekali).
  C4  Pencarian per hari dan per ruangan: jumlah kelas dan sesi yang disebut cek_jadwal = hitungan dari CSV (semua hari dan ruangan).
  C5  Sabotase dosen: N kelas dipindah ke jam sesi lain milik dosen yang sama; cek_jadwal harus menandai dosen tersebut "BENTROK"
      dan audit harus melaporkan pelanggaran (positif benar, tidak ada yang terlewat).
  C6  Sabotase ruangan: N kelas dipindah ke slot kelas lain yang ruangannya sama (ruangan berkapasitas simultan 1);
      cek_jadwal harus menandai ruangan itu "BENTROK" dan audit harus melaporkan pelanggaran.
  C7  Sabotase paket: N kelas satu paket dipindah ke jam kelas lain paket yang sama; cek_jadwal harus menandai paket itu "BENTROK".

Jadwal asli tidak diubah (sabotase hanya pada salinan di memori). Keluaran: uji_cek_jadwal.txt di data\\pemeriksaan\\uji_cekN.

Contoh:
  python src\\uji_cek_jadwal.py --jadwal data\\jadwal\\final5\\jadwal_final.csv --hasil data\\bersih\\hasil5 --n 30 --seed 1
"""
import argparse
import contextlib
import copy
import csv
import io
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from audit import Data, baca, periksa, tentukan
from cek_jadwal import HARI, Ctx, cek_hari, cek_ruangan, ringkasan
from pemodelan import Masalah


def menit(t):
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jadwal")
    ap.add_argument("--hasil")
    ap.add_argument("--n", type=int, default=30, help="jumlah sabotase tiap jenis")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keluar")
    a = ap.parse_args()
    jadwal, hasil = tentukan(a.jadwal, a.hasil)
    hasil = Path(hasil)
    d = Data(hasil)
    ms = Masalah(hasil)
    rows = baca(jadwal)
    rng = random.Random(a.seed)

    if a.keluar:
        out = Path(a.keluar)
    else:
        dasar = Path("data") / "pemeriksaan"
        n = 1
        while (dasar / f"uji_cek{n}").exists():
            n += 1
        out = dasar / f"uji_cek{n}"
    out.mkdir(parents=True, exist_ok=True)

    L, gagal = [], []

    def cek(nama, ok, ket=""):
        L.append(f"[{'LULUS' if ok else 'GAGAL'}] {nama}" + (f": {ket}" if ket else ""))
        if not ok:
            gagal.append(nama)
        print(L[-1])

    def konteks(rs):
        pel, _, iv = periksa(d, rs)
        return pel, Ctx(d, ms, iv)

    def ring(c, jenis):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ringkasan(c, jenis, out, lambda s="": None)
        with open(out / f"ringkasan_{jenis}.csv", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    pel0, c0 = konteks(rows)
    cek("Jadwal awal sah menurut audit", len(pel0) == 0, f"{len(pel0)} pelanggaran; {len(rows)} baris")

    # ---------- C1
    rd = ring(c0, "dosen")
    rr = ring(c0, "ruangan")
    rp = ring(c0, "paket")
    ndos_data = len({x for ks in d.kelas_dosen.values() for x in ks})
    ok = all(r["status"] == "OK" for r in rd + rr + rp)
    cek("C1 ringkasan dosen, ruangan, paket semuanya OK dan sepakat dengan audit (0 pelanggaran)", ok and not pel0,
        f"dosen {len(rd)}, ruangan {len(rr)}, paket {len(rp)} entri")
    cek("C1 jumlah entri sesuai data (dosen mengampu, ruangan, paket)", len(rd) == ndos_data and len(rr) == len(d.ruang) and len(rp) == len(d.paket),
        f"dosen {len(rd)}/{ndos_data}, ruangan {len(rr)}/{len(d.ruang)}, paket {len(rp)}/{len(d.paket)}")

    # ---------- C2, C3: hitungan independen dari CSV
    nama2id = {n_: i for i, n_ in d.dosen_nama.items()}
    per_kelas_dosen = defaultdict(set)
    for kid, ds in d.kelas_dosen.items():
        for x in ds:
            per_kelas_dosen[x].add(kid)
    baris_kelas = {r["kelas_id"]: r for r in rows}
    salah_kelas, salah_blok = [], []
    for r in rd:
        did = nama2id.get(r["dosen"])
        kelas_d = [k for k in per_kelas_dosen.get(did, ()) if k in baris_kelas]
        if int(r["jumlah_kelas"]) != len(kelas_d):
            salah_kelas.append(r["dosen"])
        sesi = {(baris_kelas[k]["mk_id"], baris_kelas[k]["hari"], baris_kelas[k]["jam_mulai"], baris_kelas[k]["jam_selesai"],
                 baris_kelas[k]["ruangan_id"]) for k in kelas_d}
        tot = sum((menit(s[3]) - menit(s[2])) // 50 for s in sesi)
        if int(r["total_blok"]) != tot or int(r["jumlah_sesi"]) != len(sesi):
            salah_blok.append(f"{r['dosen']} (cek {r['total_blok']} blok/{r['jumlah_sesi']} sesi, hitung {tot}/{len(sesi)})")
    cek("C2 jumlah kelas tiap dosen sama dengan hitungan dari kelas_dosen dan jadwal", not salah_kelas,
        f"{len(rd) - len(salah_kelas)}/{len(rd)} dosen sesuai" + (f"; selisih: {salah_kelas[:3]}" if salah_kelas else ""))
    cek("C3 jumlah sesi dan total blok tiap dosen sama dengan hitungan dari jam jadwal", not salah_blok,
        f"{len(rd) - len(salah_blok)}/{len(rd)} dosen sesuai" + (f"; selisih: {salah_blok[:3]}" if salah_blok else ""))

    # ---------- C4: pencarian per hari dan ruangan
    salah = []
    for h in HARI:
        buf = io.StringIO()
        cek_hari(c0, h, out, lambda s="", b=buf: b.write(s + "\n"))
        m = re.search(r"HARI \w+: (\d+) kelas dalam (\d+) sesi", buf.getvalue())
        rs = [r for r in rows if r["hari"] == h]
        sesi = {(r["mk_id"], r["jam_mulai"], r["jam_selesai"], r["ruangan_id"]) for r in rs}
        if rs and (not m or int(m.group(1)) != len(rs) or int(m.group(2)) != len(sesi)):
            salah.append(h)
    cek("C4 pencarian per hari: jumlah kelas dan sesi sama dengan hitungan dari CSV", not salah, f"{6 - len(salah)}/6 hari sesuai")
    salah = []
    for rid in d.ruang:
        buf = io.StringIO()
        cek_ruangan(c0, rid, out, lambda s="", b=buf: b.write(s + "\n"))
        m = re.search(r"(\d+) kelas dalam (\d+) sesi", buf.getvalue())
        rs = [r for r in rows if r["ruangan_id"] == rid]
        sesi = {(r["mk_id"], r["hari"], r["jam_mulai"], r["jam_selesai"]) for r in rs}
        if not m or int(m.group(1)) != len(rs) or int(m.group(2)) != len(sesi):
            salah.append(rid)
    cek("C4 pencarian per ruangan: jumlah kelas dan sesi sama dengan hitungan dari CSV", not salah,
        f"{len(d.ruang) - len(salah)}/{len(d.ruang)} ruangan sesuai")

    # ---------- sabotase
    def pindah(rs, kid, sumber, ruang=False):
        """Salinan jadwal dengan kelas kid dipindah ke hari dan jam kelas sumber (dan ruangannya bila ruang=True)."""
        rs2 = copy.deepcopy(rs)
        for r in rs2:
            if r["kelas_id"] == kid:
                for f in ("hari", "jam_mulai", "jam_selesai", "blok_awal"):
                    if f in r and f in sumber:
                        r[f] = sumber[f]
                if ruang:
                    for f in ("ruangan_id", "nama_ruangan"):
                        if f in r and f in sumber:
                            r[f] = sumber[f]
        return rs2

    # C5 dosen
    kandidat = []
    for did, ks in per_kelas_dosen.items():
        ks = [k for k in ks if k in baris_kelas]
        for x in ks:
            for y in ks:
                if x != y and baris_kelas[x]["mk_id"] != baris_kelas[y]["mk_id"] and len(d.kelas_dosen.get(x, ())) == 1:
                    kandidat.append((did, x, y))
    rng.shuffle(kandidat)
    tangkap, pel_ok, total = 0, 0, 0
    for did, x, y in kandidat[:a.n]:
        s = baris_kelas[y]
        rs2 = pindah(rows, x, s)
        pel, c = konteks(rs2)
        r_d = ring(c, "dosen")
        nm = d.dosen_nama[did]
        tandai = any(r["dosen"] == nm and r["status"] == "BENTROK" for r in r_d)
        total += 1
        tangkap += tandai
        pel_ok += bool(pel)
    cek("C5 sabotase dosen: dosen yang bentrok ditandai BENTROK oleh cek_jadwal", total > 0 and tangkap == total, f"{tangkap}/{total} terdeteksi")
    cek("C5 sabotase dosen: audit juga melaporkan pelanggaran", total > 0 and pel_ok == total, f"{pel_ok}/{total}")

    # C6 ruangan (kapasitas simultan 1)
    satu = {r for r in d.ruang if ms.ruang[r]["cap"] == 1}
    kandidat = []
    for rid in satu:
        ks = [r for r in rows if r["ruangan_id"] == rid]
        for x in ks:
            for y in ks:
                if x["kelas_id"] != y["kelas_id"] and x["mk_id"] != y["mk_id"]:
                    kandidat.append((rid, x["kelas_id"], y))
    rng.shuffle(kandidat)
    tangkap, pel_ok, total = 0, 0, 0
    for rid, x, y in kandidat[:a.n]:
        rs2 = pindah(rows, x, y, ruang=True)
        pel, c = konteks(rs2)
        r_r = ring(c, "ruangan")
        tandai = any(r["ruangan"] == d.nama_ruang.get(rid, rid) and r["status"] == "BENTROK" for r in r_r)
        total += 1
        tangkap += tandai
        pel_ok += bool(pel)
    cek("C6 sabotase ruangan: ruangan yang bentrok ditandai BENTROK oleh cek_jadwal", total > 0 and tangkap == total, f"{tangkap}/{total} terdeteksi")
    cek("C6 sabotase ruangan: audit juga melaporkan pelanggaran", total > 0 and pel_ok == total, f"{pel_ok}/{total}")

    # C7 paket
    kandidat = []
    for p, ks in d.paket.items():
        ks = [k for k in ks if k in baris_kelas]
        for x in ks:
            for y in ks:
                if x != y and baris_kelas[x]["mk_id"] != baris_kelas[y]["mk_id"]:
                    kandidat.append((p, x, y))
    rng.shuffle(kandidat)
    tangkap, total = 0, 0
    for p, x, y in kandidat[:a.n]:
        rs2 = pindah(rows, x, baris_kelas[y])
        pel, c = konteks(rs2)
        r_p = ring(c, "paket")
        tandai = any(r["paket"] == p and r["status"] == "BENTROK" for r in r_p)
        total += 1
        tangkap += tandai
    cek("C7 sabotase paket: paket yang bentrok ditandai BENTROK oleh cek_jadwal", total > 0 and tangkap == total, f"{tangkap}/{total} terdeteksi")

    L += ["", f"Jadwal: {jadwal}", f"Data: {hasil}", f"Seed sabotase: {a.seed}; jumlah per jenis: {a.n}", f"Waktu: {datetime.now():%Y-%m-%d %H:%M:%S}",
          "Perintah: " + " ".join(sys.argv),
          "Catatan: hitungan pembanding diambil langsung dari CSV jadwal dan data bersih, bukan dari fungsi cek_jadwal.py."]
    (out / "uji_cek_jadwal.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n{'SEMUA UJI LULUS' if not gagal else 'ADA UJI GAGAL: ' + ', '.join(gagal)}\nHasil di {out}")
    return 1 if gagal else 0


if __name__ == "__main__":
    sys.exit(main())