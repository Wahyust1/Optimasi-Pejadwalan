"""
Revisi jadwal interaktif (terminal): pindahkan kelas atau ganti/tukar dosen, dengan USULAN lebih dulu, bukan perubahan langsung.

Prinsip:
  - Sistem tidak pernah mengubah jadwal tanpa konfirmasi (y/n). Setiap usulan diperiksa dengan validator H1-H6 yang sama dengan GA.
  - Usulan yang melanggar hard constraint tidak pernah bisa diterapkan; tidak ada mode paksa.
  - Bila tujuan bentrok, sistem menyebut penghalangnya lalu menawarkan: (a) slot kosong lain untuk kelas itu,
    (b) rantai pemindahan: kelas penghalang dipindah ke slot lain (maks. 3 kelas, maks. 1 pemindahan ke slot yang juga bentrok),
    (c) bila penghalangnya dosen: ganti dosen. Pemilihan boleh MANUAL (Anda pilih nomor) atau OTOMATIS (sistem memilih rencana terbaik,
    tetap meminta konfirmasi).
  - Dosen pengganti hanya dosen yang menurut BKD (kelas_dosen pada data bersih) mengampu mata kuliah yang sama.
  - Jadwal asli tidak diubah. Hasil ditulis ke folder baru reviN beserta riwayat perubahan dan audit independen (audit.py).

Contoh:
  python src\\revisi_jadwal.py --jadwal data\\jadwal\\final5\\jadwal_final.csv --hasil data\\bersih\\hasil5
  python src\\revisi_jadwal.py --jadwal ... --hasil ... --otomatis
"""
import argparse
import csv
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from audit import folder_keluar, tentukan
NAMA_AUDIT = "audit"
from pemodelan import BOBOT, SUKU, Masalah, baca, baca_jadwal, tulis, tulis_jadwal
from ubah_jadwal import Hunian, skala_greedy, skor

NAMA_SUKU = [n for n, _ in SUKU]


# ------------------------------------------------------------------ pemeriksaan bentrok
def penghalang_set(h, kid, b, r):
    """Himpunan kelas yang menghalangi kelas kid di (blok awal b, ruangan r) pada hunian h. Kosong = bebas."""
    ms = h.ms
    mk = ms.kelas[kid]["mk"]
    out = set()
    for x in ms.seq_pakai(kid, b):
        occ = h.ruang.get((r, x), [])
        if occ:
            mks = {ms.kelas[o]["mk"] for o in occ}
            awal = {h.jd[o][0] for o in occ}
            cap = min(ms.ruang[r]["cap"], ms.cap_mk.get(mk, 99))
            if mks != {mk} or awal != {b} or len(occ) >= cap:
                out.update(occ)
        for d in ms.kelas_dosen.get(kid, ()):
            for o in h.dosen.get((d, x), []):
                if h.jd[o] != (b, r):
                    out.add(o)
        for p in ms.kelas_paket.get(kid, ()):
            out.update(h.paket.get((p, x), []))
    out.discard(kid)
    return out


def pos(ms, kid, br):
    b, r = br
    seq = ms.seq_pakai(kid, b)
    return f"{ms.blok[b]['hari']} {ms.blok[b]['mulai']}-{ms.blok[seq[-1]]['selesai']} di {ms.ruang[r]['nama']}"


def nm(ms, kid):
    return f"{ms.kelas[kid]['nama']} [{kid}]"


# ------------------------------------------------------------------ sesi kerja
class Sesi:
    def __init__(self, ms, jd, skala, maks_pindah=3, maks_blok=1):
        self.ms = ms
        self.jd = dict(jd)
        self.jd0 = dict(jd)
        self.skala = skala
        self.maks_pindah = maks_pindah
        self.maks_blok = maks_blok
        self.kd0 = self._salin()
        self.bkd = defaultdict(set)          # mk -> dosen yang mengampu mk itu menurut BKD (data awal)
        for kid, ds in ms.kelas_dosen.items():
            if kid in ms.kelas:
                self.bkd[ms.kelas[kid]["mk"]] |= set(ds)
        self.undo = []
        self.riwayat = []
        self.mode = "manual"
        self.lun0, self.sk0 = skor(ms, self.jd, skala)

    # ---- keadaan dosen
    def _salin(self):
        return {k: set(v) for k, v in self.ms.kelas_dosen.items()}

    def pasang_dosen(self, kid, baru):
        ms = self.ms
        for d in list(ms.kelas_dosen.get(kid, ())):
            ms.dosen_kelas[d].discard(kid)
        ms.kelas_dosen[kid] = set(baru)
        for d in baru:
            ms.dosen_kelas[d].add(kid)

    def pulih_dosen(self, kd):
        ms = self.ms
        ms.kelas_dosen = defaultdict(set, {k: set(v) for k, v in kd.items()})
        ms.dosen_kelas = defaultdict(set)
        for k, v in kd.items():
            for d in v:
                ms.dosen_kelas[d].add(k)

    def sekarang(self):
        return skor(self.ms, self.jd, self.skala)

    # ---- usulan
    def usulan(self, judul, jd_baru, moves, dosen_ubah):
        """Hitung usulan pada keadaan dosen yang berlaku SAAT INI di ms (sudah memuat dosen_ubah bila ada)."""
        ms = self.ms
        v, lun, _ = ms.validasi(jd_baru, detail=False)
        sk = sum(BOBOT[k] * lun[n] / self.skala[n] for n, k in SUKU)
        return dict(judul=judul, jd=jd_baru, moves=moves, dosen=dosen_ubah, pel=sum(v.values()), lun=lun, sk=sk, delta=sk - self._sk)

    def _dasar(self):
        self._lun, self._sk = self.sekarang()

    # ---- pencarian
    def slot_bebas(self, kid, top=5, kecuali_slot=(), dosen_ubah=None):
        """Slot lain yang bebas bentrok bila hanya kelas kid yang dipindah (keadaan saat ini)."""
        ms = self.ms
        h = Hunian(ms, self.jd, kecuali={kid})
        out = []
        for (b, r) in ms.domain[kid]:
            if (b, r) == self.jd[kid] or (b, r) in kecuali_slot:
                continue
            if penghalang_set(h, kid, b, r):
                continue
            jd2 = dict(self.jd)
            jd2[kid] = (b, r)
            out.append(self.usulan(f"Pindahkan {kid} ke slot bebas", jd2, [(kid, self.jd[kid], (b, r))], dict(dosen_ubah or {})))
        out.sort(key=lambda u: u["delta"])
        return out[:top]

    def rantai(self, jd_awal, terkunci, moves_awal, dosen_ubah, maks_rencana=8, batas_detik=6):
        """Cari rencana: kelas penghalang (konflik pada jd_awal, di luar `terkunci`) dipindah ke slot lain. Semua rencana
        yang dikembalikan lolos validator H1-H6. Pencarian bertahap (1, 2, lalu 3 kelas dipindah) dan berhenti begitu cukup rencana
        ditemukan, sehingga rencana dengan kelas dipindah paling sedikit selalu didahulukan.
        Urut: jumlah kelas dipindah terkecil, lalu selisih skor terkecil."""
        ms = self.ms
        hasil = {}
        t0 = time.time()
        habis = [False]

        def rec(jd, moves, locked, bl, lim, lihat):
            if habis[0] or time.time() - t0 > batas_detik:
                habis[0] = True
                return
            kunci = frozenset((k, jd[k]) for k, _, _ in moves)
            if kunci in lihat:
                return
            lihat.add(kunci)
            conf = ms.kelas_konflik(jd)
            if not conf:
                v, _, _ = ms.validasi(jd, detail=False)
                if sum(v.values()) == 0:
                    hasil.setdefault(kunci, (list(moves), dict(jd)))
                return
            if len(moves) - len(moves_awal) >= lim:
                return
            P = sorted(conf - locked)
            for o in P[:2]:
                h = Hunian(ms, jd, kecuali={o})
                bebas, blok = [], []
                for (b, r) in ms.domain[o]:
                    if (b, r) == jd[o]:
                        continue
                    bs = penghalang_set(h, o, b, r)
                    if not bs:
                        jd2 = dict(jd)
                        jd2[o] = (b, r)
                        lun = ms.lunak(jd2)
                        bebas.append((sum(BOBOT[k] * lun[n] / self.skala[n] for n, k in SUKU), b, r))
                    elif bl < self.maks_blok and not (bs & locked):
                        blok.append((len(bs), b, r))
                bebas.sort()
                blok.sort()
                for _, b, r in bebas[:4]:
                    jd2 = dict(jd)
                    jd2[o] = (b, r)
                    rec(jd2, moves + [(o, jd[o], (b, r))], locked | {o}, bl, lim, lihat)
                for _, b, r in blok[:3]:
                    jd2 = dict(jd)
                    jd2[o] = (b, r)
                    rec(jd2, moves + [(o, jd[o], (b, r))], locked | {o}, bl + 1, lim, lihat)

        for lim in range(1, self.maks_pindah + 1):
            rec(dict(jd_awal), list(moves_awal), set(terkunci), 0, lim, set())
            if len(hasil) >= maks_rencana or habis[0]:
                break
        out = []
        for moves, jd in hasil.values():
            judul = "Rantai: " + "; ".join(f"{k}" for k, _, _ in moves)
            out.append(self.usulan(judul, jd, moves, dosen_ubah))
        out.sort(key=lambda u: (len(u["moves"]), u["delta"]))
        return out

    # ---- operasi tingkat tinggi (mengembalikan: usulan langsung, usulan alternatif, keterangan)
    def usul_pindah(self, kid, targets, cari_rantai=True):
        ms = self.ms
        self._dasar()
        cur = self.jd[kid]
        h = Hunian(ms, self.jd, kecuali={kid})
        langsung, penghalang, luar = [], {}, {}
        for (b, r) in targets:
            if (b, r) == cur:
                continue
            if (b, r) not in ms.domain_set[kid]:
                luar[(b, r)] = ms.alasan_di_luar(kid, b, r)
                continue
            bs = penghalang_set(h, kid, b, r)
            if bs:
                penghalang[(b, r)] = bs
                continue
            jd2 = dict(self.jd)
            jd2[kid] = (b, r)
            langsung.append(self.usulan(f"Pindahkan {kid}", jd2, [(kid, cur, (b, r))], {}))
        langsung.sort(key=lambda u: u["delta"])
        alternatif = []
        if not langsung and cari_rantai:
            for (b, r) in sorted(penghalang, key=lambda x: len(penghalang[x]))[:3]:
                jd2 = dict(self.jd)
                jd2[kid] = (b, r)
                alternatif += self.rantai(jd2, {kid}, [(kid, cur, (b, r))], {})
            alternatif.sort(key=lambda u: (len(u["moves"]), u["delta"]))
            alternatif = alternatif[:5] + self.slot_bebas(kid, 5)
        return langsung, alternatif, penghalang, luar

    def kandidat_dosen(self, kid):
        mk = self.ms.kelas[kid]["mk"]
        return sorted(self.bkd[mk] - set(self.ms.kelas_dosen.get(kid, ())), key=lambda d: self.ms.nama_dosen.get(d, d))

    def usul_ganti_dosen(self, kid, lama, baru):
        """Ganti dosen `lama` -> `baru` pada kelas kid. Keadaan dosen dikembalikan setelah usulan dihitung."""
        ms = self.ms
        if baru not in self.bkd[ms.kelas[kid]["mk"]]:
            raise ValueError(f"{ms.nama_dosen.get(baru, baru)} tidak mengampu {ms.kelas[kid]['nama']} menurut BKD")
        self._dasar()
        snap = self._salin()
        try:
            ds = (set(ms.kelas_dosen[kid]) - {lama}) | {baru}
            self.pasang_dosen(kid, ds)
            ubah = {kid: set(ds)}
            conf = ms.kelas_konflik(self.jd)
            if not conf:
                return [self.usulan(f"Ganti dosen {kid}", dict(self.jd), [], ubah)], [], conf
            alt = self.rantai(dict(self.jd), {kid}, [], ubah)[:5]
            # alternatif: pindahkan kelas itu sendiri ke slot bebas bagi dosen baru
            alt += self.slot_bebas(kid, 5, dosen_ubah=ubah)
            return [], alt, conf
        finally:
            self.pulih_dosen(snap)

    def usul_tukar_dosen(self, k1, d1, k2, d2):
        ms = self.ms
        if d2 not in self.bkd[ms.kelas[k1]["mk"]] or d1 not in self.bkd[ms.kelas[k2]["mk"]]:
            raise ValueError("penukaran ditolak: salah satu dosen tidak mengampu mata kuliah tujuan menurut BKD")
        self._dasar()
        snap = self._salin()
        try:
            s1 = (set(ms.kelas_dosen[k1]) - {d1}) | {d2}
            s2 = (set(ms.kelas_dosen[k2]) - {d2}) | {d1}
            self.pasang_dosen(k1, s1)
            self.pasang_dosen(k2, s2)
            ubah = {k1: set(s1), k2: set(s2)}
            conf = ms.kelas_konflik(self.jd)
            if not conf:
                return [self.usulan("Tukar dosen", dict(self.jd), [], ubah)], [], conf
            return [], self.rantai(dict(self.jd), {k1, k2}, [], ubah)[:5], conf
        finally:
            self.pulih_dosen(snap)

    # ---- penerapan
    def terapkan(self, u, jenis, mode):
        """Terapkan usulan yang sah. Mengembalikan (True, '') atau (False, alasan). Keadaan dipulihkan bila validasi akhir gagal."""
        ms = self.ms
        if u["pel"]:
            return False, "usulan melanggar hard constraint"
        snap = (dict(self.jd), self._salin())
        lun_lama, sk_lama = self.sekarang()
        self.jd = dict(u["jd"])
        for kid, ds in u["dosen"].items():
            self.pasang_dosen(kid, ds)
        v, lun, _ = ms.validasi(self.jd, detail=False)
        if sum(v.values()):
            self.jd = snap[0]
            self.pulih_dosen(snap[1])
            return False, f"validasi akhir gagal: {dict((k, x) for k, x in v.items() if x)}"
        _, sk = self.sekarang()
        self.undo.append(snap)
        rinci = []
        for kid, f, t in u["moves"]:
            rinci.append(f"{kid}: {pos(ms, kid, f)} -> {pos(ms, kid, t)}")
        for kid, ds in u["dosen"].items():
            rinci.append(f"dosen {kid}: " + "; ".join(ms.nama_dosen.get(d, d) for d in sorted(snap[1].get(kid, ()))) +
                         " -> " + "; ".join(ms.nama_dosen.get(d, d) for d in sorted(ds)))
        self.riwayat.append(dict(waktu=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), jenis=jenis, mode=mode,
                                 kelas_dipindah=len(u["moves"]), rincian=" | ".join(rinci), skor_sebelum=round(sk_lama, 4),
                                 skor_sesudah=round(sk, 4), selisih=round(sk - sk_lama, 4)))
        return True, ""

    def batal(self):
        if not self.undo:
            return False
        jd, kd = self.undo.pop()
        self.jd = jd
        self.pulih_dosen(kd)
        if self.riwayat:
            self.riwayat.pop()
        return True


# ------------------------------------------------------------------ antarmuka terminal
class Keluar(Exception):
    pass


def tanya(teks):
    try:
        s = input(teks)
    except EOFError:
        raise Keluar()
    return s.strip()


def cari_kelas(ms, teks):
    t = teks.strip().casefold()
    if not t:
        return None
    if teks in ms.kelas:
        return teks
    kata = t.split()
    cocok = [k for k, v in ms.kelas.items() if all(w in (k + " " + v["nama"]).casefold() for w in kata)]
    if not cocok:
        print(f"  Kelas '{teks}' tidak ditemukan.")
        return None
    if len(cocok) == 1:
        return cocok[0]
    cocok.sort()
    print(f"  {len(cocok)} kelas cocok:")
    for i, k in enumerate(cocok[:20], 1):
        print(f"   {i:2d}. {nm(ms, k)}")
    if len(cocok) > 20:
        print("   ... persempit kata kunci bila kelas yang dicari tidak ada.")
    p = tanya("  Pilih nomor (kosong = batal): ")
    if p.isdigit() and 1 <= int(p) <= min(20, len(cocok)):
        return cocok[int(p) - 1]
    return None


def cari_blok(ms, hari, jam):
    jam = jam.replace(".", ":")
    if re.fullmatch(r"\d:\d\d", jam):
        jam = "0" + jam
    for b, i in ms.blok.items():
        if i["hari"].casefold() == hari.casefold() and i["mulai"] == jam:
            return b
    return None


def cari_ruang(ms, teks):
    t = teks.strip().casefold()
    for r, a in ms.ruang.items():
        if t in (r.casefold(), str(a["nama"]).casefold()):
            return r
    return None


def cetak_usulan(s, u, nomor=None):
    ms = s.ms
    if nomor is not None:
        print(f"  {nomor:2d}.")
    for kid, f, t in u["moves"]:
        print(f"     {nm(ms, kid)}")
        print(f"        {pos(ms, kid, f)}  ->  {pos(ms, kid, t)}")
    for kid, ds in u["dosen"].items():
        lama = sorted(ms.kelas_dosen.get(kid, ()))
        print(f"     dosen {nm(ms, kid)}: " + "; ".join(ms.nama_dosen.get(d, d) for d in lama) + "  ->  " +
              "; ".join(ms.nama_dosen.get(d, d) for d in sorted(ds)))
    print(f"        kelas dipindah: {len(u['moves'])}, pelanggaran keras: {u['pel']}, selisih skor bersama: {u['delta']:+.3f} (negatif = membaik)")


def rincian_skor(s, u):
    print(f"     {'Suku':14s} {'sebelum':>10s} {'sesudah':>10s}")
    lun0, _ = s.sekarang()
    for n, _ in SUKU:
        if lun0[n] != u["lun"][n]:
            print(f"     {n:14s} {lun0[n]:>10} {u['lun'][n]:>10}")


def konfirmasi_terapkan(s, u, jenis):
    print("\nUSULAN:")
    cetak_usulan(s, u)
    rincian_skor(s, u)
    jw = tanya("Terapkan perubahan ini? (y/n): ").casefold()
    if jw not in ("y", "ya"):
        print("  Dibatalkan, jadwal tidak berubah.")
        return False
    ok, ket = s.terapkan(u, jenis, s.mode)
    print("  Diterapkan." if ok else f"  GAGAL, jadwal tidak berubah: {ket}")
    return ok


def pilih_alternatif(s, alt, jenis):
    """Manual: tampilkan daftar dan minta nomor. Otomatis: pilih peringkat 1 lalu tetap minta konfirmasi."""
    if not alt:
        print("  Tidak ditemukan solusi dalam batas pencarian (maks. 3 kelas dipindah). Coba tujuan atau dosen lain.")
        return False
    if s.mode == "otomatis":
        print("  Mode otomatis: memilih rencana terbaik (kelas dipindah paling sedikit, lalu skor terbaik).")
        return konfirmasi_terapkan(s, alt[0], jenis)
    print("  Pilihan solusi:")
    for i, u in enumerate(alt, 1):
        cetak_usulan(s, u, i)
    p = tanya("  Pilih nomor (kosong = batal): ")
    if p.isdigit() and 1 <= int(p) <= len(alt):
        return konfirmasi_terapkan(s, alt[int(p) - 1], jenis)
    print("  Dibatalkan.")
    return False


def alur_pindah(s):
    ms = s.ms
    kid = cari_kelas(ms, tanya("Kelas yang dipindah (kata kunci nama atau id): "))
    if not kid:
        return
    print(f"  Saat ini: {nm(ms, kid)}\n     {pos(ms, kid, s.jd[kid])}; dosen: " +
          "; ".join(ms.nama_dosen.get(d, d) for d in sorted(ms.kelas_dosen.get(kid, ()))))
    t = tanya("Tujuan, contoh 'Senin 09:10 H17' atau 'Senin 09:10' (semua ruangan) atau 'cari' (saran slot): ")
    if not t:
        return
    if t.casefold() == "cari":
        s._dasar()
        alt = s.slot_bebas(kid, 8)
        pilih_alternatif(s, alt, "pindah_kelas")
        return
    tok = t.split()
    if len(tok) < 2:
        print("  Format: HARI JAM [RUANGAN]")
        return
    b = cari_blok(ms, tok[0], tok[1])
    if b is None:
        jam = ", ".join(i["mulai"] for x, i in ms.blok.items() if i["hari"].casefold() == tok[0].casefold())
        print(f"  Tidak ada blok yang mulai {tok[0]} {tok[1]}. Jam mulai pada hari itu: {jam or '(hari tidak dikenal)'}")
        return
    if len(tok) >= 3:
        r = cari_ruang(ms, " ".join(tok[2:]))
        if r is None:
            print("  Ruangan tidak dikenal: " + ", ".join(a["nama"] for a in ms.ruang.values()))
            return
        targets = [(b, r)]
    else:
        targets = [(b, r) for r in ms.ruang]
    langsung, alt, penghalang, luar = s.usul_pindah(kid, targets)
    if langsung:
        print(f"  Tujuan bebas bentrok ({len(langsung)} pilihan ruangan).")
        if s.mode == "otomatis" or len(langsung) == 1:
            konfirmasi_terapkan(s, langsung[0], "pindah_kelas")
        else:
            for i, u in enumerate(langsung, 1):
                print(f"   {i}. {ms.ruang[u['moves'][0][2][1]]['nama']}: selisih skor {u['delta']:+.3f}")
            p = tanya("  Pilih nomor (kosong = batal): ")
            if p.isdigit() and 1 <= int(p) <= len(langsung):
                konfirmasi_terapkan(s, langsung[int(p) - 1], "pindah_kelas")
        return
    if penghalang:
        print("  Tujuan BENTROK. Penghalang:")
        h = Hunian(ms, s.jd, kecuali={kid})
        for (bb, rr), bs in list(penghalang.items())[:4]:
            print(f"   {ms.blok[bb]['hari']} {ms.blok[bb]['mulai']} di {ms.ruang[rr]['nama']}:")
            for t_ in h.penghalang(kid, bb, rr)[:4]:
                print(f"     - {t_}")
        pilih_alternatif(s, alt, "pindah_kelas")
    else:
        for (bb, rr), a in list(luar.items())[:3]:
            print(f"  Tidak boleh menurut aturan: {a}")
        if luar:
            print("  Gunakan 'cari' untuk melihat slot yang diizinkan dan bebas bentrok.")


def pilih_dosen_kelas(s, kid, teks="Dosen yang diganti"):
    ms = s.ms
    ds = sorted(ms.kelas_dosen.get(kid, ()), key=lambda d: ms.nama_dosen.get(d, d))
    if not ds:
        print("  Kelas ini belum punya dosen.")
        return None
    if len(ds) == 1:
        return ds[0]
    for i, d in enumerate(ds, 1):
        print(f"   {i}. {ms.nama_dosen.get(d, d)}")
    p = tanya(f"  {teks} (nomor): ")
    return ds[int(p) - 1] if p.isdigit() and 1 <= int(p) <= len(ds) else None


def alur_ganti_dosen(s):
    ms = s.ms
    kid = cari_kelas(ms, tanya("Kelas yang dosennya diganti: "))
    if not kid:
        return
    lama = pilih_dosen_kelas(s, kid)
    if not lama:
        return
    kand = s.kandidat_dosen(kid)
    if not kand:
        print("  Tidak ada dosen lain yang mengampu mata kuliah ini menurut BKD.")
        return
    print(f"  Dosen pengganti menurut BKD untuk {ms.kelas[kid]['nama']}:")
    h = Hunian(ms, s.jd, kecuali={kid})
    for i, d in enumerate(kand, 1):
        b, r = s.jd[kid]
        bentrok = set()
        for x in ms.seq_pakai(kid, b):
            for o in h.dosen.get((d, x), []):
                if h.jd[o] != (b, r):
                    bentrok.add(o)
        ket = "bebas pada jam ini" if not bentrok else "bentrok dengan " + ", ".join(sorted(ms.kelas[o]["nama"] for o in bentrok)[:2])
        print(f"   {i:2d}. {ms.nama_dosen.get(d, d)}  ({len(ms.dosen_kelas.get(d, ()))} kelas; {ket})")
    p = tanya("  Pilih nomor dosen pengganti (kosong = batal): ")
    if not (p.isdigit() and 1 <= int(p) <= len(kand)):
        return
    baru = kand[int(p) - 1]
    langsung, alt, conf = s.usul_ganti_dosen(kid, lama, baru)
    if langsung:
        konfirmasi_terapkan_dosen(s, langsung[0], "ganti_dosen")
    else:
        print("  Dosen pengganti BENTROK bila kelas tetap di jam ini. Alternatif:")
        pilih_alternatif_dosen(s, alt, "ganti_dosen")


def konfirmasi_terapkan_dosen(s, u, jenis):
    # cetak_usulan memakai dosen saat ini sebagai 'lama'; sudah benar karena usulan belum diterapkan
    return konfirmasi_terapkan(s, u, jenis)


def pilih_alternatif_dosen(s, alt, jenis):
    return pilih_alternatif(s, alt, jenis)


def alur_tukar_dosen(s):
    ms = s.ms
    k1 = cari_kelas(ms, tanya("Kelas pertama: "))
    if not k1:
        return
    d1 = pilih_dosen_kelas(s, k1, "Dosen kelas pertama")
    k2 = cari_kelas(ms, tanya("Kelas kedua: "))
    if not (k2 and d1) or k1 == k2:
        return
    d2 = pilih_dosen_kelas(s, k2, "Dosen kelas kedua")
    if not d2:
        return
    if d2 not in s.bkd[ms.kelas[k1]["mk"]]:
        print(f"  Ditolak: {ms.nama_dosen.get(d2, d2)} tidak mengampu {ms.kelas[k1]['nama']} menurut BKD.")
        return
    if d1 not in s.bkd[ms.kelas[k2]["mk"]]:
        print(f"  Ditolak: {ms.nama_dosen.get(d1, d1)} tidak mengampu {ms.kelas[k2]['nama']} menurut BKD.")
        return
    langsung, alt, conf = s.usul_tukar_dosen(k1, d1, k2, d2)
    if langsung:
        konfirmasi_terapkan(s, langsung[0], "tukar_dosen")
    else:
        print("  Penukaran menimbulkan bentrok. Alternatif:")
        pilih_alternatif(s, alt, "tukar_dosen")


def alur_lihat(s):
    ms = s.ms
    t = tanya("Kata kunci kelas atau nama dosen: ")
    if not t:
        return
    kata = t.casefold().split()
    ds = [d for d, n in ms.nama_dosen.items() if all(w in n.casefold() for w in kata)]
    ks = sorted(k for k, v in ms.kelas.items() if all(w in (k + " " + v["nama"]).casefold() for w in kata))
    for d in ds[:3]:
        print(f"  Dosen {ms.nama_dosen[d]}:")
        for k in sorted(ms.dosen_kelas.get(d, ()), key=lambda k: (ms.blok_ids.index(s.jd[k][0]) if k in s.jd else 0)):
            if k in s.jd:
                print(f"     {pos(ms, k, s.jd[k]):40s} {ms.kelas[k]['nama']}")
    for k in ks[:10]:
        print(f"  {nm(ms, k)}\n     {pos(ms, k, s.jd[k])}; dosen: " + "; ".join(ms.nama_dosen.get(d, d) for d in sorted(ms.kelas_dosen.get(k, ()))))


def alur_riwayat(s):
    if not s.riwayat:
        print("  Belum ada perubahan.")
    for i, r in enumerate(s.riwayat, 1):
        print(f"  {i}. [{r['jenis']}, {r['mode']}] {r['rincian']}  (selisih skor {r['selisih']:+.3f})")


def simpan(s, jadwal, keluar):
    ms = s.ms
    out = folder_keluar(jadwal, "revisi", keluar)
    tulis_jadwal(ms, s.jd, out / "jadwal_baru.csv")
    tulis(out / "riwayat_perubahan.csv", ["waktu", "jenis", "mode", "kelas_dipindah", "rincian", "skor_sebelum", "skor_sesudah", "selisih"],
          [[r[k] for k in ("waktu", "jenis", "mode", "kelas_dipindah", "rincian", "skor_sebelum", "skor_sesudah", "selisih")] for r in s.riwayat])
    v, lun, rinci = ms.validasi(s.jd)
    tulis(out / "laporan_validasi.csv", ["indikator", "nilai"], list(v.items()) + list(lun.items()))
    # salinan data bersih dengan penugasan dosen baru, agar audit independen memeriksa keadaan yang sama
    n = ms.nomor_hasil
    dr = out / "data_revisi" / f"hasil{n}"
    shutil.copytree(ms.folder, dr)
    asal = {(r["kelas_id"], r["dosen_id"]): r for r in baca(ms.folder / f"kelas_dosen{n}.csv")}
    rows = []
    for kid in sorted(ms.kelas_dosen):
        for d in sorted(ms.kelas_dosen[kid]):
            r = asal.get((kid, d))
            rows.append([kid, d, r["peran"] if r else "", r["sumber"] if r else "revisi_jadwal"])
    tulis(dr / f"kelas_dosen{n}.csv", ["kelas_id", "dosen_id", "peran", "sumber"], rows)
    aud = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / f"{NAMA_AUDIT}.py"), "--jadwal", str(out / "jadwal_baru.csv"),
                          "--hasil", str(dr), "--keluar", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok_audit = aud.returncode == 0
    L = [f"Jadwal asal: {jadwal}", f"Jumlah perubahan: {len(s.riwayat)}",
         f"Pelanggaran keras (validator): {sum(v.values())}",
         f"Audit independen ({NAMA_AUDIT}.py): {'LOLOS' if ok_audit else 'GAGAL'}",
         f"Skor bersama: {s.sk0:.3f} -> {s.sekarang()[1]:.3f}", ""]
    L += [f"{i}. [{r['jenis']}] {r['rincian']}" for i, r in enumerate(s.riwayat, 1)]
    (out / "ringkasan.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\nHasil di {out}")
    return ok_audit


def pilih_jadwal():
    """Tanya jadwal yang direvisi: daftar folder final beserta asalnya, atau ketik jalur sendiri. Tidak ada pilihan diam-diam."""
    base = Path(__file__).resolve().parent.parent / "data"
    cand = sorted((base / "jadwal").glob("final*/jadwal_final.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    print("JADWAL YANG AKAN DIREVISI")
    for i, p in enumerate(cand, 1):
        asal = p.parent / "asal_jadwal.txt"
        sumber = ""
        if asal.exists():
            for ln in asal.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("Berkas sumber:"):
                    sumber = ln.split(":", 1)[1].strip()
        print(f"  {i}. {p.parent.name}  (sumber: {sumber or 'tidak tercatat'})")
    print("  0. ketik jalur berkas sendiri")
    while True:
        s = tanya("Pilih nomor: ")
        if s == "0":
            q = tanya("Jalur jadwal (csv): ").strip('"')
            if q and Path(q).exists():
                return q
            print("  Berkas tidak ditemukan.")
        elif s.isdigit() and 1 <= int(s) <= len(cand):
            return str(cand[int(s) - 1])
        else:
            print("  Pilihan tidak valid.")


def hasil_dari_asal(jadwal):
    asal = Path(jadwal).resolve().parent / "asal_jadwal.txt"
    if asal.exists():
        m = re.search(r"Data masukan:\s*(hasil\d+)", asal.read_text(encoding="utf-8", errors="replace"))
        if m:
            p = Path(__file__).resolve().parent.parent / "data" / "bersih" / m.group(1)
            if p.exists():
                return str(p)
    return None


def main():
    ap = argparse.ArgumentParser(description="Revisi jadwal interaktif (terminal)")
    ap.add_argument("--jadwal", help="jadwal yang direvisi; bila kosong, Anda diminta memilih dari daftar folder final")
    ap.add_argument("--hasil", help="folder data bersih, mis. data\\bersih\\hasil5")
    ap.add_argument("--keluar")
    ap.add_argument("--otomatis", action="store_true", help="mulai dalam mode otomatis (tetap meminta konfirmasi)")
    ap.add_argument("--maks-pindah", type=int, default=3)
    a = ap.parse_args()
    try:
        a.jadwal = a.jadwal or pilih_jadwal()
    except Keluar:
        print("\nMasukan berakhir.")
        return 0
    a.hasil = a.hasil or hasil_dari_asal(a.jadwal)
    jadwal, hasil = tentukan(a.jadwal, a.hasil)
    ms = Masalah(Path(hasil))
    jd = baca_jadwal(jadwal)
    s = Sesi(ms, jd, skala_greedy(ms), maks_pindah=a.maks_pindah)
    if a.otomatis:
        s.mode = "otomatis"
    v0, _, _ = ms.validasi(jd, detail=False)
    print(f"Jadwal: {jadwal}")
    asal = Path(jadwal).resolve().parent / "asal_jadwal.txt"
    if asal.exists():
        print("Asal jadwal (asal_jadwal.txt):\n  " + "\n  ".join(asal.read_text(encoding="utf-8", errors="replace").splitlines()[:6]))
    else:
        print("PERHATIAN: tidak ada asal_jadwal.txt di folder jadwal ini; pastikan ini jadwal yang Anda maksud (mis. Jadwal Final 5).")
    print(f"Kondisi awal: pelanggaran keras {sum(v0.values())}, skor bersama {s.sk0:.3f}")
    if sum(v0.values()):
        print("PERINGATAN: jadwal awal sudah melanggar hard constraint; perbaiki dahulu sebelum revisi.")
    menu = ("\nMENU\n 1 Pindahkan kelas\n 2 Ganti dosen kelas (sesuai BKD)\n 3 Tukar dosen dua kelas\n 4 Lihat jadwal kelas/dosen\n"
            " 5 Riwayat perubahan\n 6 Batalkan perubahan terakhir\n 7 Ganti mode (sekarang: {m})\n 8 Simpan dan selesai\n 0 Keluar tanpa menyimpan")
    try:
        while True:
            print(menu.format(m=s.mode))
            p = tanya("Pilih: ")
            if p == "1":
                alur_pindah(s)
            elif p == "2":
                alur_ganti_dosen(s)
            elif p == "3":
                alur_tukar_dosen(s)
            elif p == "4":
                alur_lihat(s)
            elif p == "5":
                alur_riwayat(s)
            elif p == "6":
                print("  Perubahan terakhir dibatalkan." if s.batal() else "  Tidak ada perubahan untuk dibatalkan.")
            elif p == "7":
                s.mode = "otomatis" if s.mode == "manual" else "manual"
                print(f"  Mode: {s.mode}")
            elif p == "8":
                if not s.riwayat:
                    print("  Tidak ada perubahan; tidak ada yang disimpan.")
                    continue
                return 0 if simpan(s, jadwal, a.keluar) else 1
            elif p == "0":
                print("Keluar tanpa menyimpan.")
                return 0
    except Keluar:
        print("\nMasukan berakhir; keluar tanpa menyimpan.")
        return 0


if __name__ == "__main__":
    sys.exit(main())