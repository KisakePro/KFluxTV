"""Setup de KFluxTV : installation par utilisateur (sans droits administrateur).

Usage : KFluxTV-Setup.exe            -> assistant graphique
        KFluxTV-Setup.exe /S         -> installation silencieuse (utilisée par la mise à jour)
        KFluxTV-Setup.exe /S /NORUN  -> idem, sans relancer l'application ; /D=dossier pour choisir le dossier
"""
import ctypes
import os
import subprocess
import sys
import time
import tkinter as tk
import winreg
from tkinter import filedialog, messagebox, ttk

APP = "KFluxTV"
EXE = "KFluxTV.exe"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\KFluxTV"
NOWIN = 0x08000000


def res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def version():
    try:
        return open(res("version.txt"), encoding="utf-8").read().strip()
    except Exception:
        return "0"


def default_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY) as k:
            d = winreg.QueryValueEx(k, "InstallLocation")[0]
            if d:
                return d
    except OSError:
        pass
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", APP)


def ps(script):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   creationflags=NOWIN, capture_output=True)


def q(path):
    return path.replace("'", "''")


def copy_payload(dest_dir, progress=lambda done, total: None):
    os.makedirs(dest_dir, exist_ok=True)
    src, dst = res(EXE), os.path.join(dest_dir, EXE)
    tmp = dst + ".new"
    total = os.path.getsize(src)
    done = 0
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while True:
            chunk = fi.read(1 << 20)
            if not chunk:
                break
            fo.write(chunk)
            done += len(chunk)
            progress(done, total)
    # l'ancien exe peut être encore verrouillé (appli en cours de fermeture) : on réessaie
    last = None
    for _ in range(60):
        try:
            os.replace(tmp, dst)
            return total
        except PermissionError as e:
            last = e
            time.sleep(1)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise RuntimeError("KFluxTV est en cours d'exécution : ferme-le puis relance l'installation.") from last


UNINSTALL_PS = r"""$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
$d = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([System.Windows.Forms.MessageBox]::Show('Désinstaller KFluxTV ?', 'KFluxTV', 'YesNo', 'Question') -ne 'Yes') { exit }
Get-Process KFluxTV | Stop-Process -Force
Start-Sleep 1
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'KFluxTV.lnk') -Force
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'KFluxTV.lnk') -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\KFluxTV' -Recurse -Force
if ([System.Windows.Forms.MessageBox]::Show('Supprimer aussi tes comptes et réglages ?', 'KFluxTV', 'YesNo', 'Question') -eq 'Yes') {
    Remove-Item (Join-Path $env:APPDATA 'KFluxTV') -Recurse -Force
}
Start-Process cmd -ArgumentList "/c ping -n 3 127.0.0.1 >nul & rd /s /q `"$d`"" -WindowStyle Hidden
"""


def finish_install(dest_dir, desktop, startmenu):
    exe = os.path.join(dest_dir, EXE)
    open(os.path.join(dest_dir, "KFluxTV.installed"), "w").write(version())
    with open(os.path.join(dest_dir, "uninstall.ps1"), "w", encoding="utf-8-sig") as f:
        f.write(UNINSTALL_PS)
    try:
        import shutil
        shutil.copy(res("icon.ico"), os.path.join(dest_dir, "icon.ico"))
    except Exception:
        pass
    icon = os.path.join(dest_dir, "icon.ico")
    mk = (f"$s = New-Object -ComObject WScript.Shell; "
          f"function L($p) {{ $l = $s.CreateShortcut($p); $l.TargetPath = '{q(exe)}'; "
          f"$l.WorkingDirectory = '{q(dest_dir)}'; $l.IconLocation = '{q(icon)}'; $l.Save() }}; ")
    if startmenu:
        mk += "L (Join-Path ([Environment]::GetFolderPath('Programs')) 'KFluxTV.lnk'); "
    if desktop:
        mk += "L (Join-Path ([Environment]::GetFolderPath('Desktop')) 'KFluxTV.lnk'); "
    ps(mk)
    unin = (f'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
            f'-File "{os.path.join(dest_dir, "uninstall.ps1")}"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINST_KEY) as k:
        for name, val in (("DisplayName", APP), ("DisplayVersion", version()), ("Publisher", "KisakePro"),
                          ("InstallLocation", dest_dir), ("DisplayIcon", icon), ("UninstallString", unin),
                          ("URLInfoAbout", "https://github.com/KisakePro/KFluxTV")):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, os.path.getsize(exe) // 1024)


def launch(dest_dir):
    subprocess.Popen([os.path.join(dest_dir, EXE)], cwd=dest_dir, creationflags=0x00000008, close_fds=True)


def silent():
    d = next((a[3:] for a in sys.argv if a.upper().startswith("/D=")), None) or default_dir()
    copy_payload(d)
    finish_install(d, desktop=os.path.exists(os.path.join(
        os.path.expanduser("~"), "Desktop", "KFluxTV.lnk")), startmenu=True)
    if "/NORUN" not in [a.upper() for a in sys.argv]:
        launch(d)


class Wizard(tk.Tk):
    BG, FG, MUTED, ACCENT = "#14151a", "#e6e8ef", "#8b90a0", "#7c5cff"

    def __init__(self):
        super().__init__()
        self.title(f"Installation de {APP} {version()}")
        self.geometry("560x380")
        self.resizable(False, False)
        self.configure(bg=self.BG)
        try:
            self.iconbitmap(res("icon.ico"))
        except Exception:
            pass
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("TProgressbar", troughcolor="#232632", background=self.ACCENT, bordercolor=self.BG,
                     lightcolor=self.ACCENT, darkcolor=self.ACCENT)
        tk.Label(self, text=f"📡  {APP}", font=("Segoe UI", 22, "bold"), bg=self.BG, fg=self.FG).pack(
            anchor="w", padx=28, pady=(24, 0))
        tk.Label(self, text=f"Version {version()} · lecteur de flux Xtream Codes avec Chromecast",
                 font=("Segoe UI", 10), bg=self.BG, fg=self.MUTED).pack(anchor="w", padx=30, pady=(0, 18))
        tk.Label(self, text="Dossier d'installation", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=30)
        row = tk.Frame(self, bg=self.BG)
        row.pack(fill="x", padx=30, pady=(2, 12))
        self.path = tk.StringVar(value=default_dir())
        tk.Entry(row, textvariable=self.path, bg="#1b1d24", fg=self.FG, insertbackground=self.FG, relief="flat",
                 font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(row, text="Parcourir…", command=self.browse, bg="#2a2d3a", fg=self.FG, relief="flat",
                  activebackground="#363a4d", activeforeground=self.FG, padx=10).pack(side="left", padx=(8, 0))
        self.v_desk, self.v_menu, self.v_run = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        for var, text in ((self.v_desk, "Créer un raccourci sur le Bureau"),
                          (self.v_menu, "Ajouter au menu Démarrer"),
                          (self.v_run, f"Lancer {APP} à la fin de l'installation")):
            tk.Checkbutton(self, text=text, variable=var, bg=self.BG, fg=self.FG, selectcolor="#1b1d24",
                           activebackground=self.BG, activeforeground=self.FG, font=("Segoe UI", 10)).pack(
                anchor="w", padx=26)
        self.bar = ttk.Progressbar(self, length=500)
        self.bar.pack(padx=30, pady=(18, 4))
        self.msg = tk.Label(self, text="", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9))
        self.msg.pack(anchor="w", padx=30)
        btns = tk.Frame(self, bg=self.BG)
        btns.pack(side="bottom", fill="x", padx=30, pady=18)
        self.b_inst = tk.Button(btns, text="Installer", command=self.install, bg=self.ACCENT, fg="white",
                                relief="flat", font=("Segoe UI", 10, "bold"), padx=22, pady=6,
                                activebackground="#8e72ff", activeforeground="white")
        self.b_inst.pack(side="right")
        tk.Button(btns, text="Annuler", command=self.destroy, bg="#2a2d3a", fg=self.FG, relief="flat",
                  padx=16, pady=6, activebackground="#363a4d", activeforeground=self.FG).pack(side="right", padx=8)

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.path.get())
        if d:
            self.path.set(os.path.join(d.replace("/", "\\"), APP) if os.path.basename(d).lower() != APP.lower() else d)

    def install(self):
        dest = self.path.get().strip()
        if not dest:
            return
        self.b_inst.config(state="disabled")
        self.msg.config(text="Copie des fichiers…")

        def prog(done, total):
            self.bar["value"] = done * 100 / total
            self.update_idletasks()

        try:
            copy_payload(dest, prog)
            self.msg.config(text="Création des raccourcis…")
            self.update_idletasks()
            finish_install(dest, self.v_desk.get(), self.v_menu.get())
        except Exception as e:
            self.b_inst.config(state="normal")
            self.msg.config(text="")
            return messagebox.showerror(APP, f"Échec de l'installation :\n{e}")
        self.bar["value"] = 100
        if self.v_run.get():
            launch(dest)
        messagebox.showinfo(APP, f"{APP} est installé.")
        self.destroy()


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if any(a.upper() == "/S" for a in sys.argv[1:]):
        try:
            silent()
        except Exception as e:
            ctypes.windll.user32.MessageBoxW(0, f"Échec de la mise à jour :\n{e}", APP, 0x10)
    else:
        Wizard().mainloop()
