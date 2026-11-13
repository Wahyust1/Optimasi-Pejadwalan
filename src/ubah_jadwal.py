"""
Ubah jadwal secara manual TANPA menjalankan ulang algoritma: deteksi bentrok, penjelasan, dan saran slot.

Kajur atau dosen sering ingin menggeser satu-dua kelas setelah jadwal jadi. Memeriksa jadwal jauh lebih murah daripada membuatnya:
validasi satu jadwal kurang dari satu detik, sedangkan GA beberapa menit. Skrip ini memakai validator yang sama (pemodelan.py).

Tiga kegunaan:
  --pindah KELAS HARI JAM RUANGAN   geser kelas (boleh diulang untuk beberapa kelas). Hasil: sah atau tidak, bentrok dengan siapa,
                                    dan perubahan skor S1-S6 (sebelum -> sesudah). Jadwal baru ditulis ke berkas baru.
  --mengapa KELAS                   untuk SETIAP slot yang mungkin bagi kelas itu: bisa atau tidak, dan bila tidak, kelas penghalang
                                    yang konkret ("dosen X sedang mengajar kelas Y", "ruangan penuh oleh Z", "paket bentrok dengan W").
  --saran KELAS                     slot yang bebas bentrok untuk kelas itu, diurutkan dari yang paling memperbaiki skor.

Contoh:
  python src\\ubah_jadwal.py --pindah "PSTE:kalkulus-2:B" Senin 09:10 H17
  python src\\ubah_jadwal.py --mengapa "PSTE:kalkulus-2:B"
  python src\\ubah_jadwal.py --saran "PSTE:kalkulus-2:B" --jadwal data\\ga\\ga3\\jadwal_ga3.csv
Tanpa --jadwal dipakai gaN terbaru (lalu greedyN). Keluaran ditulis ke folder ubahN di samping jadwal; jadwal asli tidak diubah.

Catatan skor: "skor bersama" = tiap suku S1-S6 dibagi rata-ratanya pada 20 jadwal greedy (seed 1-20), lalu dibobot. Skalanya perkiraan
sehingga angkanya mendekati, bukan persis sama dengan f_ref pada bandingkan.py; selisih antar-jadwal tetap bermakna.
"""
import argparse
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from audit import folder_keluar, tentukan
from pemodelan import BOBOT, SUKU, Masalah, baca_jadwal, nomor_berikut, tulis, tulis_jadwal

NAMA_SUKU = [n for n, _ in SUKU]


def skala_greedy(ms, n=20):
    from greedy import bangun
    tot = {k: 0.0 for k in NAMA_SUKU}
    pakai = 0
    for sd in range(1, n + 1):
        jd, gagal = bangun(ms, random.Random(sd))
        v, lun, _ = ms.validasi(jd, detail=False)
        if gagal or sum(v.values()):
            continue
        pakai += 1
        for k in tot:
            tot[k] += lun[k]
    if pakai == 0:
        return {k: 1.0 for k in NAMA_SUKU}
    return {k: max(1e-9, tot[k] / pakai) for k in tot}


def skor(ms, jd, skala):
    lun = ms.lunak(dict(jd))
    return lun, sum(BOBOT[k] * lun[n] / skala[n] for n, k in SUKU)


class Hunian:
    """Siapa menempati apa, dari sebuah jadwal; dipakai untuk menyebut kelas penghalang secara konkret."""

    def __init__(self, ms, jd, kecuali=()):
        self.ms = ms
        self.ruang = defaultdict(list)    # (ruangan, blok) -> [kelas]
        self.dosen = defaultdict(list)    # (dosen, blok) -> [kelas]
        self.paket = defaultdict(list)    # (paket, blok) -> [kelas]
        for kid, (b, r) in jd.items():
            if kid in kecuali:
                continue
            for x in ms.seq_pakai(kid, b):
                self.ruang[(r, x)].append(kid)
                for d in ms.kelas_dosen.get(kid, ()):
                    self.dosen[(d, x)].append(kid)
                for p in ms.kelas_paket.get(kid, ()):
                    self.paket[(p, x)].append(kid)
        self.jd = jd

    def penghalang(self, kid, b, r):
        """Daftar alasan konkret mengapa kelas kid tidak bisa di (blok awal b, ruangan r). Kosong = bisa."""
        ms = self.ms
        seq = ms.seq_pakai(kid, b)
        mk = ms.kelas[kid]["mk"]
        alasan = []
        sedikit = lambda lst: ", ".join(sorted(set(lst))[:3]) + (" dst" if len(set(lst)) > 3 else "")
        for x in seq:
            jam = f"{ms.blok[x]['hari']} {ms.blok[x]['mulai']}"
            occ = self.ruang.get((r, x), [])
            if occ:
                mks = {ms.kelas[o]["mk"] for o in occ}
                awal = {self.jd[o][0] for o in occ}
                cap = min(ms.ruang[r]["cap"], ms.cap_mk.get(mk, 99))
                if mks != {mk} or awal != {b}:
                    alasan.append(f"ruangan {ms.ruang[r]['nama']} pukul {jam} sudah dipakai {sedikit(occ)} (mata kuliah/jam mulai berbeda)")
                elif len(occ) >= cap:
                    alasan.append(f"ruangan {ms.ruang[r]['nama']} pukul {jam} penuh (kapasitas gabung {cap}): {sedikit(occ)}")
            for d in ms.kelas_dosen.get(kid, ()):
                for o in self.dosen.get((d, x), []):
                    if self.jd[o] != (b, r):
                        alasan.append(f"dosen {ms.nama_dosen.get(d, d)} pukul {jam} sedang mengajar {o}")
            for p in ms.kelas_paket.get(kid, ()):
                occ = self.paket.get((p, x), [])
                if occ:
                    alasan.append(f"paket {p} pukul {jam} sudah punya kuliah {sedikit(occ)}")
        # hapus duplikat, jaga urutan
        seen, out = set(), []
        for a in alasan:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out


def ruang_id(ms, teks):
    t = teks.strip().casefold()
    for r, a in ms.ruang.items():
        if t in (r.casefold(), str(a["nama"]).casefold()):
            return r
    sys.exit(f"Ruangan '{teks}' tidak dikenal. Contoh yang ada: " + ", ".join(f"{a['nama']} ({r})" for r, a in list(ms.ruang.items())[:6]))


def blok_id(ms, hari, jam):
    for b, i in ms.blok.items():
        if i["hari"].casefold() == hari.casefold() and i["mulai"] == jam:
            return b
    sys.exit(f"Tidak ada blok yang mulai {hari} {jam}. Jam mulai yang ada pada {hari}: " + ", ".join(i["mulai"] for b, i in ms.blok.items() if i["hari"].casefold() == hari.casefold()))


def label(ms, kid, jd):
    b, r = jd[kid]
    seq = ms.seq_pakai(kid, b)
    return f"{ms.blok[b]['hari']} {ms.blok[b]['mulai']}-{ms.blok[seq[-1]]['selesai']} di {ms.ruang[r]['nama']}"


def main():
    ap = argparse.ArgumentParser(description="Ubah jadwal manual tanpa mengulang algoritma")
    ap.add_argument("--jadwal")
    ap.add_argument("--hasil")
    ap.add_argument("--keluar", help="folder induk keluaran (bawaan: data\\pemeriksaan)")
    ap.add_argument("--pindah", nargs=4, action="append", metavar=("KELAS", "HARI", "JAM", "RUANGAN"), default=[])
    ap.add_argument("--mengapa", metavar="KELAS")
    ap.add_argument("--saran", metavar="KELAS")
    ap.add_argument("--top", type=int, default=10, help="jumlah saran slot yang ditampilkan")
    a = ap.parse_args()
    if not (a.pindah or a.mengapa or a.saran):
        sys.exit("Isi salah satu: --pindah, --mengapa, atau --saran (lihat petunjuk dengan -h).")
    jadwal, hasil = tentukan(a.jadwal, a.hasil)
    ms = Masalah(Path(hasil))
    jd0 = baca_jadwal(jadwal)
    out = folder_keluar(jadwal, "ubah", a.keluar)
    L = []
    pr = lambda s="": (L.append(s), print(s))

    def cari(kid):
        if kid in ms.kelas:
            return kid
        cocok = [k for k in ms.kelas if kid.casefold() in k.casefold()]
        if len(cocok) == 1:
            return cocok[0]
        sys.exit(f"Kelas '{kid}' " + ("tidak ditemukan." if not cocok else "ambigu: " + "; ".join(cocok[:6])))

    skala = skala_greedy(ms)
    lun0, sk0 = skor(ms, jd0, skala)
    v0, _, _ = ms.validasi(jd0, detail=False)
    pr(f"Jadwal: {jadwal}")
    pr(f"Kondisi awal: pelanggaran keras {sum(v0.values())}, skor bersama {sk0:.3f}")

    # ---------------- --pindah
    if a.pindah:
        jd = dict(jd0)
        for kid, hari, jam, rg in a.pindah:
            kid = cari(kid)
            jd[kid] = (blok_id(ms, hari, jam), ruang_id(ms, rg))
        pr("")
        pr("PERUBAHAN:")
        for kid in sorted(k for k in jd if jd[k] != jd0[k]):
            pr(f"  {kid}: {label(ms, kid, jd0)}  ->  {label(ms, kid, jd)}")
        v, lun1, rinci = ms.validasi(jd)
        sk1 = sum(BOBOT[k] * lun1[nn] / skala[nn] for nn, k in SUKU)
        pel = sum(v.values())
        pr("")
        pr(("HASIL: SAH. Tidak ada pelanggaran hard constraint." if pel == 0 else f"HASIL: TIDAK SAH. {pel} pelanggaran: {dict((k, x) for k, x in v.items() if x)}"))
        for jenis, lst in rinci.items():
            if jenis == "info":
                continue
            for t in lst[:12]:
                pr(f"  [{jenis}] {t}")
        # penjelasan konkret untuk kelas yang dipindah
        h = Hunian(ms, jd, kecuali={k for k in jd if jd[k] != jd0[k]})
        for kid in sorted(k for k in jd if jd[k] != jd0[k]):
            bl = h.penghalang(kid, *jd[kid])
            if bl:
                pr(f"  Penghalang untuk {kid} di slot baru:")
                for t in bl[:6]:
                    pr(f"    - {t}")
        pr("")
        pr(f"{'Suku':14s} {'sebelum':>10s} {'sesudah':>10s} {'selisih':>10s}  (negatif = membaik)")
        for nn, k in SUKU:
            pr(f"{nn:14s} {lun0[nn]:>10} {lun1[nn]:>10} {round(lun1[nn] - lun0[nn], 4):>10}")
        pr(f"{'skor bersama':14s} {sk0:>10.3f} {sk1:>10.3f} {sk1 - sk0:>10.3f}")
        tulis_jadwal(ms, jd, out / "jadwal_baru.csv")
        tulis(out / "laporan_validasi.csv", ["indikator", "nilai"], list(v.items()) + list(lun1.items()))
        tulis(out / "rincian_pelanggaran.csv", ["jenis", "keterangan"], [[j, t] for j, lst in rinci.items() for t in lst])
        pr("")
        pr(f"Jadwal baru: {out / 'jadwal_baru.csv'}")
        if pel:
            pr("Karena ada pelanggaran, gunakan --mengapa/--saran pada kelas yang bentrok untuk mencari slot lain.")

    # ---------------- --mengapa / --saran
    for mode, kid in (("mengapa", a.mengapa), ("saran", a.saran)):
        if not kid:
            continue
        kid = cari(kid)
        h = Hunian(ms, jd0, kecuali={kid})
        pr("")
        pr(f"KELAS {kid} (saat ini {label(ms, kid, jd0)}); dosen: " +
           "; ".join(ms.nama_dosen.get(d, d) for d in sorted(ms.kelas_dosen.get(kid, ()))) or "-")
        rows, bisa = [], []
        for (b, r) in ms.domain[kid]:
            alasan = h.penghalang(kid, b, r)
            seq = ms.seq_pakai(kid, b)
            slot = f"{ms.blok[b]['hari']} {ms.blok[b]['mulai']}-{ms.blok[seq[-1]]['selesai']}"
            if not alasan:
                jd = dict(jd0)
                jd[kid] = (b, r)
                lun1, sk1 = skor(ms, jd, skala)
                bisa.append((sk1 - sk0, slot, ms.ruang[r]["nama"], lun1))
                rows.append([slot, ms.ruang[r]["nama"], "BISA", "", round(sk1 - sk0, 4)])
            else:
                rows.append([slot, ms.ruang[r]["nama"], "TIDAK", " | ".join(alasan), ""])
        tulis(out / f"{mode}_{re.sub(r'[^A-Za-z0-9]+', '_', kid)[:50]}.csv", ["slot", "ruangan", "status", "alasan_penolakan", "selisih_skor"], rows)
        pr(f"Slot yang mungkin menurut aturan (domain): {len(ms.domain[kid])}; bebas bentrok bila kelas ini saja yang dipindah: {len(bisa)}")
        if mode == "saran":
            bisa.sort()
            pr(f"{len(bisa)} slot bebas bentrok, diurutkan dari yang paling memperbaiki skor (selisih negatif = membaik):")
            for sel, slot, rn, lun1 in bisa[:a.top]:
                pr(f"  {slot:20s} {rn:8s} selisih skor {sel:+.3f}")
        else:
            tolak = [r for r in rows if r[2] == "TIDAK"]
            hitung = defaultdict(int)
            for r in tolak:
                for t in r[3].split(" | "):
                    hitung[re.sub(r"pukul [A-Za-z]+ \d\d:\d\d ", "", t)] += 1
            pr(f"{len(tolak)} slot ditolak. Penghalang yang paling sering (jumlah slot yang diblokirnya):")
            for t, c in sorted(hitung.items(), key=lambda x: -x[1])[:8]:
                pr(f"  {c:4d} slot: {t}")
            pr("Daftar lengkap tiap slot beserta alasannya ada pada CSV di folder keluaran.")
    (out / "ringkasan.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Hasil di {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())