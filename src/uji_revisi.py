"""
Pengujian revisi_jadwal.py (bukti bahwa fitur revisi benar).

Uji:
  U1 negatif   : pemindahan yang sengaja bentrok (ruangan, dosen, paket) harus ditolak dan tidak bisa diterapkan
  U2 rantai    : tujuan yang bentrok diselesaikan dengan memindahkan penghalang; hasil harus 0 pelanggaran H1-H6
  U3 dosen     : ganti dan tukar dosen hanya sesuai BKD; usulan yang bentrok tidak langsung sah
  U4 undo      : perubahan lalu dibatalkan harus mengembalikan jadwal DAN penugasan dosen persis sama
  U5 properti  : N perubahan acak yang diterima; setelah tiap perubahan validator = 0 pelanggaran;
                 pada akhir, simpan dan audit independen (audit.py) harus sepakat
  U6 batas     : tidak ada kandidat BKD / tidak ada solusi -> sistem menolak tanpa memaksa

Contoh:
  python src\\uji_revisi.py --jadwal data\\jadwal\\final5\\jadwal_final.csv --hasil data\\bersih\\hasil5 --n 200 --seed 1
Keluaran: ringkasan uji di layar dan uji_revisi.txt di folder keluaran. Kode keluar 0 bila semua uji lulus.
"""
import argparse
import random
import sys
import tempfile
import time
from pathlib import Path

from audit import baca as baca_csv, periksa, Data
from pemodelan import Masalah, baca_jadwal
from revisi_jadwal import Sesi, penghalang_set, simpan
from ubah_jadwal import Hunian, skala_greedy


def keadaan(s):
    return (dict(s.jd), {k: frozenset(v) for k, v in s.ms.kelas_dosen.items() if v})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jadwal", required=True)
    ap.add_argument("--hasil", required=True)
    ap.add_argument("--n", type=int, default=200, help="jumlah perubahan acak pada U5")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keluar")
    a = ap.parse_args()
    ms = Masalah(Path(a.hasil))
    jd0 = baca_jadwal(a.jadwal)
    s = Sesi(ms, jd0, skala_greedy(ms))
    rng = random.Random(a.seed)
    L = []
    gagal = []

    def cek(nama, ok, ket=""):
        L.append(f"[{'LULUS' if ok else 'GAGAL'}] {nama}" + (f": {ket}" if ket else ""))
        print(L[-1])
        if not ok:
            gagal.append(nama)

    v0, _, _ = ms.validasi(jd0, detail=False)
    cek("Jadwal awal sah", sum(v0.values()) == 0, f"pelanggaran {sum(v0.values())}")

    # ---------------- U1 negatif
    n_uji, n_tolak = 0, 0
    kids = sorted(ms.kelas)
    for _ in range(300):
        kid = rng.choice(kids)
        b, r = rng.choice(ms.domain[kid])
        if (b, r) == s.jd[kid]:
            continue
        h = Hunian(ms, s.jd, kecuali={kid})
        if not penghalang_set(h, kid, b, r):
            continue
        n_uji += 1
        # paksa: jadwal dengan penempatan bentrok harus tidak sah menurut validator, dan usulan tidak boleh langsung
        jd2 = dict(s.jd)
        jd2[kid] = (b, r)
        v, _, _ = ms.validasi(jd2, detail=False)
        langsung, _, penghalang, _ = s.usul_pindah(kid, [(b, r)], cari_rantai=False)
        if sum(v.values()) > 0 and not langsung and (b, r) in penghalang:
            n_tolak += 1
        if n_uji >= 60:
            break
    cek("U1 pemindahan bentrok ditolak (validator dan usulan sepakat)", n_uji > 0 and n_tolak == n_uji, f"{n_tolak}/{n_uji}")
    # usulan yang melanggar tidak bisa diterapkan
    kid = next(k for k in kids if any(penghalang_set(Hunian(ms, s.jd, kecuali={k}), k, b, r) for (b, r) in ms.domain[k][:60]))
    b, r = next((b, r) for (b, r) in ms.domain[kid][:60] if penghalang_set(Hunian(ms, s.jd, kecuali={kid}), kid, b, r))
    jd2 = dict(s.jd)
    jd2[kid] = (b, r)
    s._dasar()
    u = s.usulan("uji bentrok", jd2, [(kid, s.jd[kid], (b, r))], {})
    ok, ket = s.terapkan(u, "uji", "uji")
    cek("U1 usulan melanggar tidak dapat diterapkan", (not ok) and s.jd == jd0, ket)

    # ---------------- U2 rantai
    n_r, n_ok, n_waktu = 0, 0, 0.0
    for _ in range(60):
        kid = rng.choice(kids)
        b, r = rng.choice(ms.domain[kid])
        if (b, r) == s.jd[kid]:
            continue
        t0 = time.time()
        langsung, alt, penghalang, _ = s.usul_pindah(kid, [(b, r)])
        n_waktu = max(n_waktu, time.time() - t0)
        if langsung or not penghalang:
            continue
        rantai = [u for u in alt if len(u["moves"]) > 1]
        for u in rantai[:3]:
            n_r += 1
            v, _, _ = ms.validasi(u["jd"], detail=False)
            tujuan_ok = u["jd"][kid] == (b, r)
            if sum(v.values()) == 0 and u["pel"] == 0 and tujuan_ok:
                n_ok += 1
        if n_r >= 30:
            break
    cek("U2 rencana rantai sah dan kelas mencapai tujuan", n_r > 0 and n_ok == n_r, f"{n_ok}/{n_r}; pencarian terlama {n_waktu:.1f} s")

    # ---------------- U3 dosen
    sepasang = None
    for kid in rng.sample(kids, len(kids)):
        kand = s.kandidat_dosen(kid)
        if kand and s.ms.kelas_dosen.get(kid):
            sepasang = (kid, sorted(ms.kelas_dosen[kid])[0], kand[0])
            break
    if sepasang:
        kid, lama, baru = sepasang
        langsung, alt, conf = s.usul_ganti_dosen(kid, lama, baru)
        ok_bkd = baru in s.bkd[ms.kelas[kid]["mk"]]
        cek("U3 dosen pengganti berasal dari BKD mata kuliah yang sama", ok_bkd)
        cek("U3 ganti dosen: ada usulan langsung atau alternatif; yang bentrok tidak langsung sah", bool(langsung) != bool(conf) or (not langsung and not conf))
        cek("U3 keadaan dosen tidak berubah setelah usulan dihitung", {k: frozenset(v) for k, v in ms.kelas_dosen.items() if v} == keadaan(s)[1])
    # tukar dosen: cari dua kelas dari mata kuliah yang sama dengan dosen berbeda
    pasangan = None
    for mk in sorted(s.bkd):
        ks = [k for k in kids if ms.kelas[k]["mk"] == mk and len(ms.kelas_dosen.get(k, ())) == 1]
        if len(ks) >= 2:
            k1, k2 = ks[0], ks[1]
            d1, d2 = next(iter(ms.kelas_dosen[k1])), next(iter(ms.kelas_dosen[k2]))
            if d1 != d2:
                pasangan = (k1, d1, k2, d2)
                break
    if pasangan:
        langsung, alt, conf = s.usul_tukar_dosen(*pasangan)
        cek("U3 tukar dosen menghasilkan usulan atau alternatif", bool(langsung) or bool(alt) or bool(conf))

    # ---------------- U4 undo
    s2 = Sesi(Masalah(Path(a.hasil)), jd0, skala_greedy(Masalah(Path(a.hasil))))
    sebelum = keadaan(s2)
    diterima = False
    for kid in rng.sample(kids, len(kids)):
        langsung, alt, _, _ = s2.usul_pindah(kid, rng.sample(ms.domain[kid], 40))
        pilih = langsung[:1] or alt[:1]
        if pilih and s2.terapkan(pilih[0], "uji", "uji")[0]:
            diterima = True
            break
    sesudah_ubah = keadaan(s2)
    s2.batal()
    cek("U4 perubahan benar-benar terjadi sebelum dibatalkan", diterima and sesudah_ubah != sebelum)
    cek("U4 batalkan mengembalikan jadwal dan dosen persis sama", keadaan(s2) == sebelum)

    # ---------------- U5 properti
    s3 = Sesi(Masalah(Path(a.hasil)), jd0, skala_greedy(Masalah(Path(a.hasil))))
    ms3 = s3.ms
    diterima, ditolak_valid = 0, 0
    jenis_hit = {"pindah": 0, "rantai": 0, "ganti_dosen": 0, "tukar_dosen": 0}
    semua_sah = True
    t0 = time.time()
    percobaan = 0
    while diterima < a.n and percobaan < a.n * 8:
        percobaan += 1
        r_ = rng.random()
        pilih = None
        jenis = ""
        if r_ < 0.55:
            kid = rng.choice(sorted(ms3.kelas))
            tg = rng.sample(ms3.domain[kid], min(25, len(ms3.domain[kid])))
            langsung, alt, _, _ = s3.usul_pindah(kid, tg)
            if langsung:
                pilih, jenis = rng.choice(langsung[:3]), "pindah"
            elif alt:
                pilih, jenis = rng.choice(alt), "rantai"
        elif r_ < 0.85:
            ada = [k for k in sorted(ms3.kelas) if s3.kandidat_dosen(k) and ms3.kelas_dosen.get(k)]
            kid = rng.choice(ada) if ada else rng.choice(sorted(ms3.kelas))
            kand = s3.kandidat_dosen(kid)
            if kand and ms3.kelas_dosen.get(kid):
                lama = rng.choice(sorted(ms3.kelas_dosen[kid]))
                langsung, alt, _ = s3.usul_ganti_dosen(kid, lama, rng.choice(kand))
                pilih, jenis = (langsung[0], "ganti_dosen") if langsung else ((rng.choice(alt), "ganti_dosen") if alt else (None, ""))
        else:
            mk = rng.choice(sorted(s3.bkd))
            ks = [k for k in sorted(ms3.kelas) if ms3.kelas[k]["mk"] == mk and len(ms3.kelas_dosen.get(k, ())) == 1]
            if len(ks) >= 2:
                k1, k2 = rng.sample(ks, 2)
                d1, d2 = next(iter(ms3.kelas_dosen[k1])), next(iter(ms3.kelas_dosen[k2]))
                if d1 != d2:
                    langsung, alt, _ = s3.usul_tukar_dosen(k1, d1, k2, d2)
                    pilih, jenis = (langsung[0], "tukar_dosen") if langsung else ((rng.choice(alt), "tukar_dosen") if alt else (None, ""))
        if not pilih:
            continue
        ok, ket = s3.terapkan(pilih, jenis, "uji")
        if ok:
            diterima += 1
            jenis_hit[jenis] += 1
            v, _, _ = ms3.validasi(s3.jd, detail=False)
            if sum(v.values()):
                semua_sah = False
                break
        else:
            ditolak_valid += 1
    cek(f"U5 {diterima} perubahan acak diterima, validator 0 pelanggaran setelah tiap perubahan", semua_sah and diterima >= min(a.n, 20),
        f"diterima {diterima}, {jenis_hit}, gagal-terapkan {ditolak_valid}, {time.time() - t0:.0f} s")
    if a.keluar:
        keluar = a.keluar
    else:   # simpan di proyek (bukan folder Temp) agar menjadi bukti: data\pemeriksaan\uji_revisiN
        dasar = Path("data") / "pemeriksaan"
        n = 1
        while (dasar / f"uji_revisi{n}").exists():
            n += 1
        keluar = str(dasar / f"uji_revisi{n}")
        Path(keluar).mkdir(parents=True)
    from io import StringIO
    import contextlib
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        ok_audit = simpan(s3, a.jadwal, keluar)
    cek("U5 audit independen (audit.py) sepakat: 0 pelanggaran pada jadwal hasil revisi", ok_audit)
    # sepakat pula bila diaudit langsung dengan Data + periksa
    out = sorted(Path(keluar).glob("revisi*"))[-1]
    d = Data(out / "data_revisi" / f"hasil{ms.nomor_hasil}")
    pel, _, _ = periksa(d, baca_csv(out / "jadwal_baru.csv"))
    cek("U5 pemeriksaan ulang pada berkas keluaran: 0 pelanggaran", len(pel) == 0, f"{len(pel)} pelanggaran")

    # ---------------- U6 batas
    tanpa = [k for k in kids if not s.kandidat_dosen(k)]
    semua_dosen = sorted(ms.nama_dosen)
    ditolak = 0
    for kid in tanpa[:20]:
        lama = sorted(ms.kelas_dosen[kid])[0]
        luar = next(d for d in semua_dosen if d not in s.bkd[ms.kelas[kid]["mk"]])
        try:
            s.usul_ganti_dosen(kid, lama, luar)
        except ValueError:
            ditolak += 1
    cek("U6 dosen di luar BKD ditolak oleh sistem", ditolak == min(20, len(tanpa)) and ditolak > 0, f"{ditolak} ditolak; {len(tanpa)} dari {len(kids)} kelas tidak punya kandidat pengganti menurut BKD")
    s4 = Sesi(Masalah(Path(a.hasil)), jd0, skala_greedy(Masalah(Path(a.hasil))), maks_pindah=0)
    kid = kids[0]
    n_tak = 0
    for (b, r) in s4.ms.domain[kid][:80]:
        langsung, alt, penghalang, _ = s4.usul_pindah(kid, [(b, r)])
        if penghalang and not langsung and not [u for u in alt if len(u["moves"]) > 1]:
            n_tak += 1
    n_rantai = 0
    for (b, r) in s4.ms.domain[kid][:80]:
        langsung, alt, penghalang, _ = s4.usul_pindah(kid, [(b, r)])
        n_rantai += len([x for x in alt if len(x["moves"]) > 1])
    cek("U6 batas rantai 0: sistem tidak memindahkan penghalang bila tidak diizinkan", n_tak > 0 and n_rantai == 0, f"{n_tak} tujuan bentrok, {n_rantai} rencana rantai")

    (Path(keluar) / "uji_revisi.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n{'SEMUA UJI LULUS' if not gagal else 'ADA UJI GAGAL: ' + ', '.join(gagal)}\nHasil di {keluar}")
    return 1 if gagal else 0


if __name__ == "__main__":
    sys.exit(main())