"""
Tahap 4 - Keluaran jadwal: jadwal per prodi, per dosen (dengan kelas yang diajar), per hari, per ruangan, per paket.

Dipakai dengan dua cara, hasilnya sama:
  1. Otomatis di akhir greedy.py dan ga.py (ditulis ke subfolder laporan/ pada folder run).
  2. Mandiri, untuk jadwal apa pun (hasil greedy, hasil GA, atau jadwal buatan tangan):
       python src/laporan.py --jadwal data/ga/ga3/jadwal_ga3.csv
       python src/laporan.py --jadwal data/greedy/greedy2/jadwal_greedy2.csv --hasil hasil3
     Berkas jadwal minimal berisi kolom: kelas_id, blok_awal, ruangan_id.

Keluaran (folder laporan/ di samping berkas jadwal):
  jadwal_PSTE.csv, jadwal_PSTI.csv, jadwal_MTE.csv    jadwal tiap prodi, urut hari dan jam
  dosen_per_prodi.csv                                 tiap dosen: kelas, mata kuliah, SKS, hari, jam, ruangan
  dosen_ringkasan.csv                                 jumlah kelas, total SKS, dan beban per hari tiap dosen
  jadwal_per_hari.csv                                 seluruh kelas, urut hari lalu jam
  jadwal_per_ruangan.csv                              pemakaian tiap ruangan
  jadwal_per_paket.csv                                jadwal kelas wajib per paket semester (jeda dan bentrok mudah dilihat)
  grid_paket.csv, grid_ruangan.csv                    tampilan grid jam x hari per paket dan per ruangan (seperti jadwal cetak)
  ringkasan_jadwal.txt                                angka ringkas
  laporan_jadwal.xlsx                                 semua di atas sebagai lembar Excel
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from pemodelan import BOBOT, SUKU, Masalah, baca, baca_jadwal, tulis

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"]
URUT_HARI = {h: i for i, h in enumerate(HARI)}


def _baris(ms, jadwal):
    """Satu baris per kelas terjadwal, dengan jam mulai/selesai dari seluruh blok yang dipakai."""
    sesi = defaultdict(int)
    for kid, (b, r) in jadwal.items():
        sesi[(ms.kelas[kid]["mk"], b, r)] += 1
    rows = []
    for kid, (b, r) in jadwal.items():
        k = ms.kelas[kid]
        seq = ms.seq_pakai(kid, b)
        rows.append(dict(
            kelas_id=kid, prodi=k["prodi"], semester=k["semester"], jenis=k["jenis"], kelas=k["nama"], sks=k["sks"],
            hari=ms.blok[b]["hari"], mulai=ms.blok[seq[0]]["mulai"], selesai=ms.blok[seq[-1]]["selesai"],
            ruangan_id=r, ruangan=ms.ruang[r]["nama"], blok_awal=b,
            dosen_id=sorted(ms.kelas_dosen[kid]),
            dosen="; ".join(ms.nama_dosen.get(d, d) for d in sorted(ms.kelas_dosen[kid])),
            gabung=sesi[(k["mk"], b, r)], paket="; ".join(sorted(ms.kelas_paket.get(kid, ()))),
        ))
    rows.sort(key=lambda x: (URUT_HARI.get(x["hari"], 9), x["mulai"], x["prodi"], x["kelas"]))
    return rows


def buat_laporan(ms, jadwal, folder):
    """Tulis semua berkas laporan ke `folder` (dibuat bila belum ada). Mengembalikan dict nama -> (header, baris)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = _baris(ms, jadwal)
    kepala = ["hari", "jam_mulai", "jam_selesai", "prodi", "semester", "jenis", "kelas", "sks", "ruangan", "dosen",
              "kelas_digabung", "paket"]
    def umum(x):
        return [x["hari"], x["mulai"], x["selesai"], x["prodi"], x["semester"], x["jenis"], x["kelas"], x["sks"],
                x["ruangan"], x["dosen"], x["gabung"], x["paket"]]

    lembar = {}
    for pr in sorted({x["prodi"] for x in rows}):
        lembar[f"jadwal_{pr}"] = (kepala, [umum(x) for x in rows if x["prodi"] == pr])
    lembar["jadwal_per_hari"] = (kepala, [umum(x) for x in rows])

    # dosen per prodi: satu baris per (dosen, kelas)
    dp = []
    for x in rows:
        for d in x["dosen_id"]:
            dp.append((ms.nama_dosen.get(d, d), x["prodi"], x["kelas"], x["jenis"], x["sks"], x["hari"], x["mulai"],
                       x["selesai"], x["ruangan"], x["gabung"]))
    dp.sort(key=lambda t: (t[1], t[0], URUT_HARI.get(t[5], 9), t[6]))
    lembar["dosen_per_prodi"] = (["dosen", "prodi", "kelas", "jenis", "sks", "hari", "jam_mulai", "jam_selesai", "ruangan",
                                  "kelas_digabung"], [list(t) for t in dp])

    # ringkasan beban dosen: satu baris per (dosen, prodi); SKS dihitung sekali per sesi (kelas gabung tidak dihitung ganda)
    agg = defaultdict(lambda: dict(kelas=0, sks=0.0, hari=defaultdict(float)))
    for x in rows:
        for d in x["dosen_id"]:
            a = agg[(d, x["prodi"])]
            a["kelas"] += 1
            porsi = x["sks"] / x["gabung"]   # sesi gabungan dihitung satu kali dengan membagi rata ke kelas anggotanya
            a["sks"] += porsi
            a["hari"][x["hari"]] += porsi
    rs = []
    for (d, pr), a in sorted(agg.items(), key=lambda kv: (kv[0][1], ms.nama_dosen.get(kv[0][0], kv[0][0]))):
        rs.append([ms.nama_dosen.get(d, d), pr, a["kelas"], round(a["sks"], 2)] + [round(a["hari"].get(h, 0), 2) for h in HARI[:5]])
    lembar["dosen_ringkasan"] = (["dosen", "prodi", "jumlah_kelas", "sks_sesi", "Senin", "Selasa", "Rabu", "Kamis", "Jumat"], rs)

    # per ruangan
    rr = sorted(rows, key=lambda x: (x["ruangan"], URUT_HARI.get(x["hari"], 9), x["mulai"]))
    lembar["jadwal_per_ruangan"] = (["ruangan", "hari", "jam_mulai", "jam_selesai", "prodi", "kelas", "sks", "dosen", "kelas_digabung"],
                                    [[x["ruangan"], x["hari"], x["mulai"], x["selesai"], x["prodi"], x["kelas"], x["sks"], x["dosen"],
                                      x["gabung"]] for x in rr])

    # per paket (kelas wajib dengan label paket)
    pk = []
    for x in rows:
        for p in x["paket"].split("; "):
            if p:
                pk.append([p, x["hari"], x["mulai"], x["selesai"], x["kelas"], x["sks"], x["ruangan"], x["dosen"]])
    pk.sort(key=lambda t: (t[0], URUT_HARI.get(t[1], 9), t[2]))
    lembar["jadwal_per_paket"] = (["paket", "hari", "jam_mulai", "jam_selesai", "kelas", "sks", "ruangan", "dosen"], pk)

    # tampilan grid (seperti jadwal cetak): baris = jam, kolom = hari; satu blok per sel
    hari_ada = [h for h in HARI if any(ms.blok[b]["hari"] == h for b in ms.blok_ids)]
    slot = sorted({(ms.blok[b]["mulai"], ms.blok[b]["selesai"]) for b in ms.blok_ids})
    def grid(kunci_fn, teks_fn, judul):
        sel = defaultdict(list)   # (judul, jam, hari) -> teks
        for kid, (b, r) in jadwal.items():
            for x in ms.seq_pakai(kid, b):
                for j in kunci_fn(kid, r):
                    sel[(j, (ms.blok[x]["mulai"], ms.blok[x]["selesai"]), ms.blok[x]["hari"])].append(teks_fn(kid, r))
        out_rows = []
        for j in sorted({k[0] for k in sel}):
            for (m, se) in slot:
                isi = [" | ".join(sorted(sel.get((j, (m, se), h), []))) for h in hari_ada]
                if any(isi):
                    out_rows.append([j, f"{m}-{se}"] + isi)
        return ([judul, "jam"] + hari_ada, out_rows)
    lembar["grid_paket"] = grid(lambda kid, r: sorted(ms.kelas_paket.get(kid, ())),
                                lambda kid, r: f"{ms.kelas[kid]['nama']} [{ms.ruang[r]['nama']}]", "paket")
    lembar["grid_ruangan"] = grid(lambda kid, r: [ms.ruang[r]["nama"]],
                                  lambda kid, r: f"{ms.kelas[kid]['nama']} ({ms.kelas[kid]['prodi']})", "ruangan")

    # kelas terjadwal (daftar datar, memuat kelas_id) dan kelas yang belum terjadwal
    lembar["kelas_terjadwal"] = (["kelas_id", "prodi", "semester", "jenis", "kelas", "sks", "hari", "jam_mulai", "jam_selesai",
                                  "ruangan", "dosen", "kelas_digabung", "paket"],
                                 [[x["kelas_id"], x["prodi"], x["semester"], x["jenis"], x["kelas"], x["sks"], x["hari"], x["mulai"],
                                   x["selesai"], x["ruangan"], x["dosen"], x["gabung"], x["paket"]]
                                  for x in sorted(rows, key=lambda x: (x["prodi"], str(x["semester"]), x["kelas"]))])
    belum = [kid for kid in ms.kelas if kid not in jadwal]
    lembar["kelas_belum_terjadwal"] = (["kelas_id", "prodi", "semester", "jenis", "kelas", "sks"],
                                       [[kid, ms.kelas[kid]["prodi"], ms.kelas[kid]["semester"], ms.kelas[kid]["jenis"],
                                         ms.kelas[kid]["nama"], ms.kelas[kid]["sks"]] for kid in belum])

    # blok kosong per ruangan: tiap (ruangan, blok) yang boleh dipakai, dengan isi dan sisa kapasitas
    isi = defaultdict(list)
    for kid, (b, r) in jadwal.items():
        for x in ms.seq_pakai(kid, b):
            isi[(r, x)].append(kid)
    bk, ringk_r, per_hari_tot = [], [], defaultdict(lambda: [0, 0])
    for r, a_ in sorted(ms.ruang.items(), key=lambda kv: kv[1]["nama"]):
        tersedia = [x for x in ms.blok_ids if a_["hari"] is None or ms.blok[x]["hari"].casefold() in a_["hari"]]
        tersedia.sort(key=lambda x: (URUT_HARI.get(ms.blok[x]["hari"], 9), ms.blok[x]["mulai"]))
        terisi = 0
        for x in tersedia:
            ks = isi.get((r, x), [])
            terisi += 1 if ks else 0
            per_hari_tot[ms.blok[x]["hari"]][0] += 1
            per_hari_tot[ms.blok[x]["hari"]][1] += 1 if ks else 0
            bk.append([a_["nama"], ms.blok[x]["hari"], ms.blok[x]["mulai"], ms.blok[x]["selesai"], a_["cap"], len(ks),
                       max(0, a_["cap"] - len(ks)), "terisi" if ks else "KOSONG",
                       "; ".join(sorted(ms.kelas[k]["nama"] for k in ks))])
        ringk_r.append([a_["nama"], a_["cap"], len(tersedia), terisi, len(tersedia) - terisi,
                        round(100 * terisi / len(tersedia), 1) if tersedia else 0])
    lembar["blok_kosong_ruangan"] = (["ruangan", "hari", "jam_mulai", "jam_selesai", "kapasitas_kelas", "kelas_terisi", "sisa_kapasitas",
                                      "status", "isi"], bk)
    lembar["ruangan_ringkasan"] = (["ruangan", "kapasitas_kelas", "blok_tersedia", "blok_terisi", "blok_kosong", "persen_terisi"], ringk_r)
    lembar["hari_ringkasan"] = (["hari", "blok_ruangan_tersedia", "blok_ruangan_terisi", "persen_terisi", "jumlah_kelas"],
                                [[h, t, p, round(100 * p / t, 1) if t else 0, sum(1 for x in rows if x["hari"] == h)]
                                 for h, (t, p) in sorted(per_hari_tot.items(), key=lambda kv: URUT_HARI.get(kv[0], 9))])

    for nama, (h, b) in lembar.items():
        tulis(folder / f"{nama}.csv", h, b)

    # ringkasan
    per_prodi = defaultdict(int)
    per_hari = defaultdict(int)
    for x in rows:
        per_prodi[x["prodi"]] += 1
        per_hari[x["hari"]] += 1
    baris = ["RINGKASAN JADWAL", f"Kelas terjadwal: {len(rows)} dari {len(ms.kelas)}",
             "Per prodi: " + ", ".join(f"{k} {v}" for k, v in sorted(per_prodi.items())),
             "Per hari: " + ", ".join(f"{h} {per_hari.get(h, 0)}" for h in HARI),
             f"Dosen yang mengajar: {len({d for x in rows for d in x['dosen_id']})}",
             f"Sesi gabungan (lebih dari satu kelas): {len({(ms.kelas[x['kelas_id']]['mk'], x['blok_awal'], x['ruangan_id']) for x in rows if x['gabung'] > 1})}"]
    (folder / "ringkasan_jadwal.txt").write_text("\n".join(baris) + "\n", encoding="utf-8")

    # analisis jadwal: pelanggaran, suku lunak, sebaran, jeda paket, beban dosen
    v, lun, _ = ms.validasi(jadwal, detail=False)
    jeda = []
    for p, ks in ms.paket.items():
        pos = defaultdict(set)
        for kid in ks:
            if kid in jadwal:
                for x in ms.seq_pakai(kid, jadwal[kid][0]):
                    pos[ms.blok[x]["hari"]].add(ms.posisi[x])
        for h, ps in pos.items():
            g = (max(ps) - min(ps) + 1) - len(ps)
            if g:
                jeda.append((g, p, h))
    jeda.sort(reverse=True)
    sore = [x for x in rows if x["jenis"] == "wajib" and x["selesai"] > "16:20"]
    dos = sorted(((sum(a_["hari"].values()), d) for (d, _p), a_ in agg.items()), reverse=True)
    hmaks = sorted(((max(a_["hari"].values()), d) for (d, _p), a_ in agg.items()), reverse=True)
    L = ["ANALISIS JADWAL", "",
         f"Kelas terjadwal: {len(rows)} dari {len(ms.kelas)}",
         "Pelanggaran keras: " + ", ".join(f"{k}={n}" for k, n in v.items()) + f"  (jumlah {sum(v.values())})", "",
         "Suku lunak (makin kecil makin baik):"]
    for n, k in SUKU:
        L.append(f"  {n:14s} {lun[n]:>10}   bobot {BOBOT[k]}")
    L.append(f"  f_berbobot (mentah) = {lun['f_berbobot']}")
    L += ["", "Sebaran kelas per hari: " + ", ".join(f"{h} {sum(1 for x in rows if x['hari'] == h)}" for h in HARI[:5]),
          "Pemakaian ruangan (blok terisi / tersedia): " + "; ".join(f"{r[0]} {r[3]}/{r[2]} ({r[5]}%)" for r in ringk_r),
          f"Blok ruangan kosong: {sum(r[4] for r in ringk_r)} dari {sum(r[2] for r in ringk_r)} (rincian: blok_kosong_ruangan.csv)", "",
          f"Kelas wajib berakhir setelah 16:20: {len(sore)} kelas",
          f"Jeda kosong kelas wajib per paket-hari: total {sum(g for g, _, _ in jeda)} blok pada {len(jeda)} paket-hari"]
    for g, p, h in jeda[:8]:
        L.append(f"  paket {p}, {h}: {g} blok kosong di antara kelas")
    L += ["", "Dosen dengan total SKS sesi terbanyak: " + "; ".join(f"{ms.nama_dosen.get(d, d)} ({round(t, 1)})" for t, d in dos[:5]),
          "Dosen dengan beban satu hari tertinggi (SKS sesi): " + "; ".join(f"{ms.nama_dosen.get(d, d)} ({round(t, 1)})" for t, d in hmaks[:5])]
    (folder / "analisis_jadwal.txt").write_text("\n".join(L) + "\n", encoding="utf-8")

    # Excel (opsional bila openpyxl tersedia)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        wb.remove(wb.active)
        for nama, (h, b) in lembar.items():
            ws = wb.create_sheet(nama[:31])
            ws.append(h)
            for c in ws[1]:
                c.font = Font(bold=True)
            for r in b:
                ws.append(r)
            ws.freeze_panes = "A2"
            for i, kol in enumerate(zip(*([h] + b)) if b else [h], start=1):
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(60, max(len(str(v)) for v in kol) + 2)
        wb.save(folder / "laporan_jadwal.xlsx")
    except ImportError:
        pass
    return lembar


def _cari_hasil(jadwal_path, hasil):
    if hasil:
        return hasil
    # status berkas di folder run mencatat SUMBER: hasilN
    for st in Path(jadwal_path).parent.glob("status*.txt"):
        m = re.search(r"SUMBER:\s*(hasil\d+)", st.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return None


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Laporan jadwal: per prodi, dosen, hari, ruangan, paket")
    ap.add_argument("--jadwal", required=True, help="berkas jadwal CSV (kolom kelas_id, blok_awal, ruangan_id)")
    ap.add_argument("--hasil", help="folder pembersihan hasilN (bawaan: dibaca dari status di folder jadwal)")
    ap.add_argument("--bersih", default=str(base / "bersih"))
    a = ap.parse_args()
    jp = Path(a.jadwal)
    hasil = _cari_hasil(jp, a.hasil)
    if not hasil:
        sys.exit("Tidak tahu memakai hasilN yang mana; tambahkan --hasil hasilN")
    ms = Masalah(Path(a.bersih) / hasil)
    jd = baca_jadwal(jp)
    keluar = jp.parent / "laporan"
    buat_laporan(ms, jd, keluar)
    print(f"{len(jd)} kelas dibaca dari {jp.name}; laporan di {keluar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())