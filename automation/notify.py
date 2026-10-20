"""
notify.py — Popup notifikasi bersama untuk SMRO, SERP, dan Pur Info Reg.

FIX: sebelumnya main.py (dipakai juga oleh automation_pur_info_reg.py lewat
`smro.show_popup`) dan automation_serp.py punya implementasi show_popup()
SENDIRI-SENDIRI dengan tampilan berbeda:
  - main.py / Pur Info Reg -> tkinter.messagebox polos (harus klik OK manual)
  - automation_serp.py     -> Toplevel custom (ikon warna, auto-close countdown)

Sekarang ketiganya memakai satu implementasi yang sama dari modul ini, supaya
notifikasi "berhasil"/"gagal" terlihat identik untuk SMRO, SERP, maupun
Pur Info Reg.
"""
import logging
import tkinter as tk
from tkinter import messagebox


def show_popup(title: str, message: str, success: bool = True,
                auto_close_ms: int = 4000, enabled: bool = True) -> None:
    """
    Tampilkan popup notifikasi dengan desain seragam untuk semua proses.

    - title/message : isi notifikasi.
    - success        : True -> ikon centang hijau, False -> ikon silang merah.
    - auto_close_ms  : tutup otomatis setelah N milidetik (default 4 detik).
                       Set ke 0 untuk tidak auto-close (tunggu user klik OK).
    - enabled        : kalau False, popup di-skip dan hanya dicatat ke log
                       (dipakai saat dijalankan headless/tanpa GUI, mis. lewat
                       Run All sequential yang set *_SHOW_POPUP=false).
    """
    if not enabled:
        logging.info(f"[Popup] {title}: {message}")
        return
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        if auto_close_ms > 0:
            # Auto-close: Toplevel sendiri supaya bisa dipanggil after()
            win = tk.Toplevel(root)
            win.title(title)
            win.attributes('-topmost', True)
            win.resizable(False, False)

            # Ikon: ✔ hijau untuk sukses, ✖ merah untuk error
            icon_char = "✔" if success else "✖"
            icon_color = "#27ae60" if success else "#e74c3c"
            tk.Label(win, text=icon_char, font=("Segoe UI", 28), fg=icon_color,
                     bg="white", padx=16, pady=8).grid(row=0, column=0, sticky="n")
            tk.Label(win, text=message, font=("Segoe UI", 10),
                     bg="white", justify="left", wraplength=340,
                     padx=16, pady=8).grid(row=0, column=1, sticky="w")

            countdown_var = tk.StringVar(value=f"Menutup dalam {auto_close_ms // 1000}s...")
            lbl_cd = tk.Label(win, textvariable=countdown_var,
                              font=("Segoe UI", 8), fg="#888", bg="white")
            lbl_cd.grid(row=1, column=0, columnspan=2, pady=(0, 4))

            btn = tk.Button(win, text="OK", width=10, command=win.destroy,
                            font=("Segoe UI", 9))
            btn.grid(row=2, column=0, columnspan=2, pady=(0, 10))

            win.configure(bg="white")
            win.update_idletasks()
            # Posisi tengah layar
            w, h = win.winfo_width(), win.winfo_height()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

            remaining_ms = [auto_close_ms]

            def _tick():
                remaining_ms[0] -= 1000
                if remaining_ms[0] <= 0:
                    try:
                        win.destroy()
                    except Exception:
                        pass
                    return
                secs = remaining_ms[0] // 1000
                countdown_var.set(f"Menutup dalam {secs}s...")
                try:
                    win.after(1000, _tick)
                except Exception:
                    pass

            win.after(1000, _tick)
            win.wait_window()
        else:
            if success:
                messagebox.showinfo(title, message)
            else:
                messagebox.showerror(title, message)

        root.destroy()
    except Exception as e:
        logging.error(f"show_popup error: {e}")
