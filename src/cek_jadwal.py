"""
Cek jadwal: "bertanya" pada sebuah jadwal tanpa membuka CSV satu per satu.

Untuk satu dosen, kelas, mata kuliah, ruangan, paket mahasiswa, atau hari: tampilkan jadwalnya, kalimat kesimpulan (ada bentrok atau
tidak, dan mengapa), dan kartu gambar jadwal mingguan. Untuk SEMUA dosen/ruangan/paket sekaligus: tabel ringkasan dengan kolom bentrok.
Jadwal tidak diubah; untuk menggeser kelas gunakan ubah_jadwal.py.

Contoh:
  python src\\cek_jadwal.py --dosen "Nadia"
  python src\\cek_jadwal.py --mk kalkulus matematika-diskrit kecerdasan-buatan
  python src\\cek_jadwal.py --kelas "kalkulus-2:B"
  python src\\cek_jadwal.py --ruangan H17
  python src\\cek_jadwal.py --paket PSTE-S2-B
  python src\\cek_jadwal.py --hari Senin
  python src\\cek_jadwal.py --ringkasan dosen          # atau ruangan, paket
Tanpa --jadwal dipakai gaN terbaru (lalu greedyN); --jadwal boleh berupa folder, berkas CSV, atau jadwal_final.csv.
Keluaran: folder cekN di samping jadwal (kartu PNG dan CSV); jadwal asli tidak diubah.

Aturan bentrok (sama dengan audit_hard.py, dihitung dari JAM NYATA): dua kelas bertumpang tindih bila hari sama dan intervalnya beririsan.
Dua kelas dari mata kuliah yang sama pada hari, jam, dan ruangan yang sama dihitung SATU sesi gabungan (dosen mengajar sekali), bukan bentrok.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from audit import Data, baca, folder_keluar, menit, periksa, r_fmt, tentukan, tumpang
from pemodelan import Masalah, tulis

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


class Ctx:
    def __init__(self, d, ms, iv):
        self.d, self.ms, self.iv = d, ms, iv

    def nama_mk(self, kid):
        k = self.d.kelas[kid]
        return (self.d.mk.get(k["mk_id"], {}).get("nama_mk") or k.get("nama_kelas_mentah") or k["mk_id"]).strip()

    def nama(self, kid):
        lab = (self.d.kelas[kid].get("label_paralel") or "").strip()
        return f"{self.nama_mk(kid)} {lab}".strip()

    def dosen_str(self, kid):
        return "; ".join(sorted(self.d.dosen_nama.get(x, x) for x in self.d.kelas_dosen.get(kid, ()))) or "-"

    def sesi_id(self, kid):
        h, m1, m2, r, mk, _ = self.iv[kid]
        return (mk, h, m1, m2, r)

    def sesi(self, ids):
        """Kelas -> sesi (kelas gabungan = satu sesi). Urut hari lalu jam."""
        g = defaultdict(list)
        for kid in ids:
            if kid in self.iv:
                g[self.sesi_id(kid)].append(kid)
        out = [(sid, sorted(v)) for sid, v in g.items()]
        out.sort(key=lambda x: (HARI.index(x[0][1]) if x[0][1] in HARI else 9, x[0][2]))
        return out

    def rn(self, r):
        return self.d.nama_ruang.get(r, r)


def konflik_antar_sesi(sesi_list):
    """Pasangan sesi yang beririsan waktunya."""
    out = []
    for i in range(len(sesi_list)):
        for j in range(i + 1, len(sesi_list)):
            a, b = sesi_list[i][0], sesi_list[j][0]
            if tumpang((a[1], a[2], a[3]), (b[1], b[2], b[3])):
                out.append((sesi_list[i], sesi_list[j]))
    return out


def baris_sesi(c, s, tampil_dosen=True):
    sid, kids = s
    mk, h, m1, m2, r = sid
    nama = c.nama(kids[0]) if len(kids) == 1 else f"{c.nama_mk(kids[0])} " + "+".join((c.d.kelas[k].get('label_paralel') or '').strip() for k in kids)
    gab = f"  [gabungan {len(kids)} paralel]" if len(kids) > 1 else ""
    dos = f"  dosen: {c.dosen_str(kids[0])}" if tampil_dosen else ""
    return f"  {h:<7} {r_fmt(m1)}-{r_fmt(m2)}  {c.rn(r):<10} {nama}{gab}{dos}"


# ----------------------------------------------------------------------- gambar
WARNA_PRODI = {"PSTE": "#bcd4ee", "PSTI": "#f7cfa6", "MTE": "#d6c3ea"}  # biru, oranye, ungu (merah dicadangkan untuk bentrok)
WARNA_LAIN = "#d9d9d9"
WARNA_CAMPUR = "#bfe3d6"


def WARNA_CAMPUR_ADA(sesi_list, c):
    return any(len({str(c.d.kelas[k].get("prodi") or "?") for k in kids}) > 1 for _, kids in sesi_list)


def kartu(path, judul, sesi_list, kon, c, ok_teks, warna_ok="#cfe8cf"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        return False
    hari_pakai = [h for h in HARI[:6]]
    semua_m = [x for s, _ in sesi_list for x in (s[2], s[3])] or [450, 1050]
    lo, hi = min(semua_m + [450]) - 20, max(semua_m + [1020]) + 20
    fig, ax = plt.subplots(figsize=(13, 7.5))
    ax.set_xlim(0, len(hari_pakai))
    ax.set_ylim(hi, lo)
    ax.set_xticks([i + 0.5 for i in range(len(hari_pakai))])
    ax.set_xticklabels(hari_pakai, fontsize=11, fontweight="bold")
    ax.xaxis.tick_top()
    jam = list(range((lo // 60 + 1) * 60, hi, 60))
    ax.set_yticks(jam)
    ax.set_yticklabels([r_fmt(j) for j in jam], fontsize=8)
    for i in range(len(hari_pakai) + 1):
        ax.axvline(i, color="#cccccc", lw=0.8)
    for j in jam:
        ax.axhline(j, color="#eeeeee", lw=0.6)
    merah = set()
    pakai = set()
    for a, b in kon:
        merah.add(a[0])
        merah.add(b[0])
    for (sid, kids) in sesi_list:
        mk, h, m1, m2, r = sid
        if h not in hari_pakai:
            continue
        x = hari_pakai.index(h)
        gabung = len(kids) > 1
        pr_set = sorted({str(c.d.kelas[k].get("prodi") or "?") for k in kids})
        pm = {p for p in pr_set if p in WARNA_PRODI}
        if sid in merah:
            fc = "#f4b6b6"
        elif len(pr_set) == 1:
            fc = WARNA_PRODI.get(pr_set[0], WARNA_LAIN)
        else:
            fc = WARNA_CAMPUR
        pakai.update(pr_set)
        ax.add_patch(Rectangle((x + 0.04, m1), 0.92, m2 - m1, facecolor=fc, edgecolor="#d9534f" if sid in merah else "#444444",
                               lw=1.8 if sid in merah else 0.9, ls="--" if (gabung and sid not in merah) else "-", alpha=0.95))
        nm = c.nama_mk(kids[0])
        lab = "+".join((c.d.kelas[k].get("label_paralel") or "").strip() for k in kids).strip("+")
        baris2 = (lab + (" (gabungan)" if gabung else "") + (" [" + "+".join(pr_set) + "]" if len(pr_set) > 1 else "")).strip()
        teks = f"{nm[:26]}\n" + (baris2 + "\n" if baris2 else "") + f"{c.rn(r)}  {r_fmt(m1)}-{r_fmt(m2)}"
        ax.text(x + 0.5, (m1 + m2) / 2, teks, ha="center", va="center", fontsize=7.5)
    from matplotlib.patches import Patch
    hnd = [Patch(fc=WARNA_PRODI.get(p, WARNA_LAIN), ec="#444", label=f"prodi {p}") for p in sorted(pakai) if p in WARNA_PRODI or p == "?"]
    if any(p not in WARNA_PRODI and p != "?" for p in pakai):
        hnd.append(Patch(fc=WARNA_LAIN, ec="#444", label="prodi lain"))
    if WARNA_CAMPUR_ADA(sesi_list, c):
        hnd.append(Patch(fc=WARNA_CAMPUR, ec="#444", label="gabungan lintas prodi"))
    hnd += [Patch(fc="white", ec="#444", ls="--", label="garis putus = sesi gabungan (dihitung 1 sesi)"), Patch(fc="#f4b6b6", ec="#d9534f", label="BENTROK")]
    ax.legend(handles=hnd, loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=len(hnd), fontsize=8, frameon=False)
    ax.set_title(judul + "\n" + ok_teks, fontsize=11, pad=34, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True


def aman(t):
    return re.sub(r"[^A-Za-z0-9]+", "_", t)[:40].strip("_")


# ----------------------------------------------------------------------- entitas
def cek_dosen(c, kata, out, pr):
    d = c.d
    cocok = [x for x, n in d.dosen_nama.items() if kata.casefold() in n.casefold() or kata.casefold() == x.casefold()]
    cocok = [x for x in cocok if any(x in d.kelas_dosen.get(k, ()) for k in c.iv)]
    if not cocok:
        pr(f"\nDosen '{kata}' tidak ditemukan pada jadwal.")
        return
    if len(cocok) > 4:
        pr(f"\nDosen '{kata}' cocok dengan {len(cocok)} nama; persempit: " + "; ".join(d.dosen_nama[x] for x in cocok[:8]))
        return
    for dos in cocok:
        ids = [k for k in c.iv if dos in d.kelas_dosen.get(k, ())]
        sl = c.sesi(ids)
        kon = konflik_antar_sesi(sl)
        beban = Counter()
        for sid, kids in sl:
            beban[sid[1]] += (sid[3] - sid[2]) // 50
        pr(f"\nDOSEN: {d.dosen_nama[dos]}")
        pr(f"  Mengampu {len(ids)} kelas, {len(sl)} sesi pengajaran, datang ke kampus {len(beban)} hari. Beban per hari (blok): "
           + ", ".join(f"{h} {beban[h]}" for h in HARI if beban.get(h)))
        for s in sl:
            pr(baris_sesi(c, s, tampil_dosen=False))
        gab = [s for s in sl if len(s[1]) > 1]
        if gab:
            pr(f"  Catatan: {len(gab)} sesi adalah gabungan beberapa paralel (dosen mengajar sekali): " + "; ".join(c.nama_mk(s[1][0]) for s in gab))
        if kon:
            ok = f"ADA BENTROK: {len(kon)} pasang sesi beririsan"
            for a, b in kon:
                pr(f"  !! BENTROK: {c.nama(a[1][0])} {a[0][1]} {r_fmt(a[0][2])}-{r_fmt(a[0][3])}  dan  {c.nama(b[1][0])} {b[0][1]} {r_fmt(b[0][2])}-{r_fmt(b[0][3])}")
        else:
            ok = f"TIDAK ADA BENTROK: tidak ada dua dari {len(sl)} sesi yang jamnya beririsan"
        pr("  => " + ok)
        kartu(out / f"dosen_{aman(d.dosen_nama[dos])}.png", f"Jadwal dosen: {d.dosen_nama[dos]}", sl, kon, c, ok)


def cek_ruangan(c, kata, out, pr):
    d, ms = c.d, c.ms
    cocok = [r for r in d.ruang if kata.casefold() in (r.casefold(), str(c.rn(r)).casefold())]
    if not cocok:
        cocok = [r for r in d.ruang if kata.casefold() in str(c.rn(r)).casefold()]
    if not cocok:
        pr(f"\nRuangan '{kata}' tidak dikenal. Yang ada: " + ", ".join(c.rn(r) for r in d.ruang))
        return
    for r in cocok:
        ids = [k for k in c.iv if c.iv[k][3] == r]
        sl = c.sesi(ids)
        # bentrok: dua sesi berbeda yang beririsan pada ruangan yang sama (sesi gabungan sudah satu sesi)
        kon = konflik_antar_sesi(sl)
        a = ms.ruang[r]
        pakai, tersedia = defaultdict(set), defaultdict(int)
        for b, i in ms.blok.items():
            if a["hari"] is None or i["hari"].casefold() in a["hari"]:
                tersedia[i["hari"]] += 1
        for k in ids:
            for x in ms.seq_pakai(k, c.iv[k][5]):
                pakai[ms.blok[x]["hari"]].add(x)
        pr(f"\nRUANGAN: {c.rn(r)} ({r}); kapasitas simultan {a['cap']}; hari diizinkan: {'semua' if a['hari'] is None else ', '.join(sorted(a['hari']))}")
        tot_t = sum(tersedia.values())
        tot_p = sum(len(v) for v in pakai.values())
        pr(f"  {len(ids)} kelas dalam {len(sl)} sesi; blok terpakai {tot_p} dari {tot_t} tersedia ({100 * tot_p / max(tot_t, 1):.0f}%), blok kosong {tot_t - tot_p}")
        pr("  Blok kosong per hari: " + ", ".join(f"{h} {tersedia[h] - len(pakai.get(h, ()))}/{tersedia[h]}" for h in HARI if tersedia.get(h)))
        for s in sl:
            pr(baris_sesi(c, s, tampil_dosen=True))
        ok = (f"ADA BENTROK: {len(kon)} pasang sesi beririsan di ruangan ini" if kon else
              f"TIDAK ADA BENTROK: tidak ada dua sesi berbeda yang memakai {c.rn(r)} pada jam yang sama")
        pr("  => " + ok)
        kartu(out / f"ruangan_{aman(c.rn(r))}.png", f"Pemakaian ruangan {c.rn(r)}", sl, kon, c, ok)


def cek_paket(c, kata, out, pr):
    d, ms = c.d, c.ms
    cocok = [p for p in d.paket if kata.casefold() in p.casefold()]
    if not cocok:
        pr(f"\nPaket '{kata}' tidak ditemukan. Contoh: " + ", ".join(sorted(d.paket)[:8]))
        return
    if len(cocok) > 4:
        pr(f"\nPaket '{kata}' cocok dengan {len(cocok)}: " + ", ".join(sorted(cocok)[:10]) + " ... persempit.")
        return
    for p in cocok:
        ids = [k for k in d.paket[p] if k in c.iv]
        sl = c.sesi(ids)
        kon = konflik_antar_sesi(sl)
        pr(f"\nPAKET: {p} ({len(ids)} kelas wajib; mahasiswa satu paket mengambil semuanya)")
        per_hari = defaultdict(list)
        for s in sl:
            per_hari[s[0][1]].append(s)
        for h in HARI:
            if h not in per_hari:
                continue
            urut = sorted(per_hari[h], key=lambda x: x[0][2])
            jeda = sum(max(0, urut[i + 1][0][2] - urut[i][0][3]) for i in range(len(urut) - 1))
            pr(f"  {h}: {len(urut)} kuliah, jeda total di antara kuliah {jeda} menit" if len(urut) > 1 else f"  {h}: 1 kuliah")
        for s in sl:
            pr(baris_sesi(c, s, tampil_dosen=True))
        ok = (f"ADA BENTROK: {len(kon)} pasang kuliah beririsan; mahasiswa harus di dua kelas sekaligus" if kon else
              f"TIDAK ADA BENTROK: tidak ada dua kuliah paket {p} yang jamnya beririsan")
        pr("  => " + ok)
        kartu(out / f"paket_{aman(p)}.png", f"Jadwal paket mahasiswa {p}", sl, kon, c, ok)


def cek_mk(c, kata_list, out, pr):
    d = c.d
    for kata in kata_list:
        ids = sorted(k for k in c.iv if kata.casefold() in d.kelas[k]["mk_id"].casefold() or kata.casefold() in (d.kelas[k].get("nama_kelas_mentah") or "").casefold())
        if not ids:
            pr(f"\nMata kuliah '{kata}' tidak ditemukan.")
            continue
        pr(f"\nMATA KULIAH '{kata}': {len(ids)} kelas")
        sl = c.sesi(ids)
        for s in sl:
            pr(baris_sesi(c, s))
        dosen = sorted({x for k in ids for x in d.kelas_dosen.get(k, ())})
        pr("  Pemeriksaan tiap dosen pengampu pada SELURUH jadwalnya (bukan hanya mata kuliah ini):")
        for dos in dosen:
            semua = [k for k in c.iv if dos in d.kelas_dosen.get(k, ())]
            sdos = c.sesi(semua)
            kon = konflik_antar_sesi(sdos)
            pr(f"    {d.dosen_nama[dos]}: {len(semua)} kelas, {len(sdos)} sesi -> " + (f"BENTROK {len(kon)}" if kon else "tidak ada bentrok"))
        kon_mk = konflik_antar_sesi(sl)
        # dua paralel mata kuliah yang sama pada jam yang sama BEDA ruangan boleh (dosen berbeda)? tetap dilaporkan bila dosen sama
        pr("  => " + ("Seluruh dosen mata kuliah ini bebas bentrok." if not any(konflik_antar_sesi(c.sesi([k for k in c.iv if x in d.kelas_dosen.get(k, ())])) for x in dosen)
                       else "ADA dosen yang bentrok (lihat di atas)."))
        kartu(out / f"matakuliah_{aman(kata)}.png", f"Mata kuliah '{kata}': semua paralel", sl, [], c,
              "Warna kuning = sesi gabungan paralel; hijau = kelas tunggal")


def cek_kelas(c, kata, out, pr):
    d, ms = c.d, c.ms
    ids = [k for k in c.iv if kata.casefold() in k.casefold() or kata.casefold() in c.nama(k).casefold()]
    if not ids:
        pr(f"\nKelas '{kata}' tidak ditemukan.")
        return
    if len(ids) > 5:
        pr(f"\nKelas '{kata}' cocok dengan {len(ids)}; persempit, contoh: " + "; ".join(sorted(ids)[:6]))
        return
    from ubah_jadwal import Hunian
    jd = {k: (c.iv[k][5], c.iv[k][3]) for k in c.iv}
    for kid in sorted(ids):
        k = d.kelas[kid]
        h, m1, m2, r, mk, ba = c.iv[kid]
        pr(f"\nKELAS: {kid}")
        pr(f"  {c.nama(kid)}  | prodi {k['prodi']} | semester {k.get('semester') or '-'} | jenis {k.get('jenis_mk') or '-'} | {k.get('durasi_blok') or k.get('sks')} SKS")
        pr(f"  Jadwal: {h} {r_fmt(m1)}-{r_fmt(m2)} di {c.rn(r)}")
        pr(f"  Dosen: {c.dosen_str(kid)}")
        pasangan = [x for x in c.iv if x != kid and c.sesi_id(x) == c.sesi_id(kid)]
        pr("  Disatukan dengan paralel: " + (", ".join(c.nama(x) for x in sorted(pasangan)) if pasangan else "tidak (sesi tunggal)"))
        pk = sorted(p for p, ks in d.paket.items() if kid in ks)
        pr("  Paket mahasiswa: " + (", ".join(pk) if pk else "-"))
        for dos in sorted(d.kelas_dosen.get(kid, ())):
            lain = c.sesi([x for x in c.iv if dos in d.kelas_dosen.get(x, ()) and x != kid])
            hr = [s for s in lain if s[0][1] == h]
            pr(f"  {d.dosen_nama.get(dos, dos)} pada hari {h} juga mengajar: " + ("; ".join(f"{c.nama_mk(s[1][0])} {r_fmt(s[0][2])}-{r_fmt(s[0][3])}" for s in hr) or "tidak ada"))
        hun = Hunian(ms, jd, kecuali={kid})
        bisa = sum(1 for (b, rr) in ms.domain[kid] if not hun.penghalang(kid, b, rr))
        pr(f"  Mengapa di sini? Dari {len(ms.domain[kid])} slot yang diizinkan aturan, hanya {bisa} yang bebas bentrok bila kelas ini saja dipindah "
           f"(rincian: ubah_jadwal.py --mengapa \"{kid}\").")


def cek_hari(c, kata, out, pr):
    h = next((x for x in HARI if x.casefold() == kata.casefold()), None)
    if not h:
        pr(f"\nHari '{kata}' tidak dikenal.")
        return
    ids = [k for k in c.iv if c.iv[k][0] == h]
    sl = c.sesi(ids)
    pr(f"\nHARI {h}: {len(ids)} kelas dalam {len(sl)} sesi")
    pr("  Per prodi: " + ", ".join(f"{p} {n}" for p, n in sorted(Counter(c.d.kelas[k]['prodi'] for k in ids).items())))
    pr("  Per ruangan: " + ", ".join(f"{c.rn(r)} {n}" for r, n in sorted(Counter(c.iv[k][3] for k in ids).items(), key=lambda x: -x[1])))
    for s in sl:
        pr(baris_sesi(c, s))
    kon_semua = 0
    pr("  (Bentrok per dosen, ruangan, dan paket diperiksa pada jadwal utuh: python src\\audit_hard.py)")


def ringkasan(c, jenis, out, pr):
    d, ms = c.d, c.ms
    rows = []
    if jenis == "dosen":
        kepala = ["dosen", "jumlah_kelas", "jumlah_sesi", "hari_mengajar", "beban_harian_maks_blok", "total_blok", "pasang_bentrok", "status"]
        for dos in sorted(d.dosen_nama, key=lambda x: d.dosen_nama[x]):
            ids = [k for k in c.iv if dos in d.kelas_dosen.get(k, ())]
            if not ids:
                continue
            sl = c.sesi(ids)
            kon = konflik_antar_sesi(sl)
            beban = Counter()
            for sid, kids in sl:
                beban[sid[1]] += (sid[3] - sid[2]) // 50
            rows.append([d.dosen_nama[dos], len(ids), len(sl), len(beban), max(beban.values()), sum(beban.values()), len(kon),
                         "OK" if not kon else "BENTROK"])
    elif jenis == "ruangan":
        kepala = ["ruangan", "jumlah_sesi", "blok_tersedia", "blok_terpakai", "blok_kosong", "persen_terpakai", "pasang_bentrok", "status"]
        for r in d.ruang:
            ids = [k for k in c.iv if c.iv[k][3] == r]
            sl = c.sesi(ids)
            kon = konflik_antar_sesi(sl)
            a = ms.ruang[r]
            tersedia = sum(1 for b, i in ms.blok.items() if a["hari"] is None or i["hari"].casefold() in a["hari"])
            pakai = {x for k in ids for x in ms.seq_pakai(k, c.iv[k][5])}
            rows.append([c.rn(r), len(sl), tersedia, len(pakai), tersedia - len(pakai), round(100 * len(pakai) / max(tersedia, 1), 1),
                         len(kon), "OK" if not kon else "BENTROK"])
    else:
        kepala = ["paket", "jumlah_kelas", "hari_kuliah", "jeda_total_menit", "pasang_bentrok", "status"]
        for p in sorted(d.paket):
            ids = [k for k in d.paket[p] if k in c.iv]
            sl = c.sesi(ids)
            kon = konflik_antar_sesi(sl)
            per = defaultdict(list)
            for s in sl:
                per[s[0][1]].append(s)
            jeda = 0
            for lst in per.values():
                u = sorted(lst, key=lambda x: x[0][2])
                jeda += sum(max(0, u[i + 1][0][2] - u[i][0][3]) for i in range(len(u) - 1))
            rows.append([p, len(ids), len(per), jeda, len(kon), "OK" if not kon else "BENTROK"])
    tulis(out / f"ringkasan_{jenis}.csv", kepala, rows)
    bad = [r for r in rows if r[-1] != "OK"]
    pr(f"\nRINGKASAN {jenis.upper()}: {len(rows)} entri, bentrok: {len(bad)}")
    pr("  " + "  ".join(f"{h:>14}" if i else f"{h:<32}" for i, h in enumerate(kepala)))
    for r in rows[:18]:
        pr("  " + "  ".join(f"{str(x):>14}" if i else f"{str(x)[:32]:<32}" for i, x in enumerate(r)))
    if len(rows) > 18:
        pr(f"  ... ({len(rows) - 18} baris lain di ringkasan_{jenis}.csv)")
    pr("  => " + ("Semua " + jenis + " bebas bentrok." if not bad else f"{len(bad)} {jenis} bentrok: " + "; ".join(str(r[0]) for r in bad[:6])))


def main():
    ap = argparse.ArgumentParser(description="Cek jadwal per dosen, kelas, mata kuliah, ruangan, paket, atau hari")
    ap.add_argument("--jadwal")
    ap.add_argument("--hasil")
    ap.add_argument("--keluar", help="folder induk keluaran (bawaan: data\\pemeriksaan)")
    ap.add_argument("--dosen", nargs="*", default=[])
    ap.add_argument("--kelas", nargs="*", default=[])
    ap.add_argument("--mk", nargs="*", default=[])
    ap.add_argument("--ruangan", nargs="*", default=[])
    ap.add_argument("--paket", nargs="*", default=[])
    ap.add_argument("--hari", nargs="*", default=[])
    ap.add_argument("--ringkasan", choices=["dosen", "ruangan", "paket"], nargs="*", default=[])
    a = ap.parse_args()
    if not any([a.dosen, a.kelas, a.mk, a.ruangan, a.paket, a.hari, a.ringkasan]):
        sys.exit("Isi salah satu: --dosen, --kelas, --mk, --ruangan, --paket, --hari, atau --ringkasan (petunjuk: -h).")
    jadwal, hasil = tentukan(a.jadwal, a.hasil)
    d = Data(hasil)
    ms = Masalah(Path(hasil))
    rows = baca(jadwal)
    pel, stat, iv = periksa(d, rows)
    c = Ctx(d, ms, iv)
    out = folder_keluar(jadwal, "cek", a.keluar)
    L = []
    pr = lambda s="": (L.append(s), print(s))
    pr(f"Jadwal: {jadwal}  ({len(iv)} dari {len(d.kelas)} kelas terjadwal; pelanggaran hard constraint menurut audit: {len(pel)})")
    for x in a.ringkasan:
        ringkasan(c, x, out, pr)
    for x in a.dosen:
        cek_dosen(c, x, out, pr)
    if a.mk:
        cek_mk(c, a.mk, out, pr)
    for x in a.kelas:
        cek_kelas(c, x, out, pr)
    for x in a.ruangan:
        cek_ruangan(c, x, out, pr)
    for x in a.paket:
        cek_paket(c, x, out, pr)
    for x in a.hari:
        cek_hari(c, x, out, pr)
    (out / "hasil_cek.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nHasil dan kartu gambar di {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())