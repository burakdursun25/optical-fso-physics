"""FSO Fizik Motoru – başlatıcı.

Kullanım:
    python run.py                 # sunucuyu başlat (http://127.0.0.1:8000), değişiklikte yeniden yükle
    python run.py --open          # başlat ve tarayıcıda aç
    python run.py --port 8080     # farklı port
    python run.py --no-reload     # otomatik yeniden yükleme kapalı
    python run.py --check         # bağımlılık ve modül kontrolü, sunucu başlatmaz
    python run.py --test          # pytest çalıştır
    python run.py --report        # test raporu + grafikler (reports/test_report.md, reports/figures/)
    python run.py --exit          # çalışan sunucuyu durdur (port 8000)
"""

import argparse
import importlib
import importlib.util
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REQUIRED = ["fastapi", "uvicorn", "numpy", "pydantic"]
OPTIONAL = {"matplotlib": "test grafikleri (--report)", "pytest": "testler (--test)", "httpx": "API testleri"}

BACKEND_MODULES = [
    "backend.engine", "backend.optics", "backend.numerics", "backend.materials",
    "backend.bitcheck", "backend.propagation3d", "backend.arq", "backend.duplex",
    "backend.comsol", "backend.app",
]

FRONTEND_FILES = [
    "index.html", "style.css", "ui-v2.css", "engine.js", "comsol.js", "renderer.js",
    "app.js", "viz3d.js", "duplex.js", "lens-designer.js",
]

PANELS = [
    ("Ana panel (2B ışın izleme, link bütçesi)", ""),
    ("3B Yayılım / bozunum bulutu / lens kesiti", "#v3-wrap"),
    ("API sağlık kontrolü", "api/health"),
    ("Lens kataloğu (malzeme, filtre, kaplama)", "api/lens/catalog"),
    ("Etkileşimli API dokümantasyonu", "docs"),
]


def check_environment(verbose=True):
    ok = True
    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        ok = False
        print(f"[HATA] Eksik paketler: {', '.join(missing)}")
        print("       Kurulum: pip install -r requirements-dev.txt")
    for mod, why in OPTIONAL.items():
        if importlib.util.find_spec(mod) is None and verbose:
            print(f"[uyarı] {mod} kurulu değil – {why} çalışmaz.")

    missing_files = [f for f in FRONTEND_FILES if not (ROOT / f).exists()]
    if missing_files:
        ok = False
        print(f"[HATA] Eksik arayüz dosyaları: {', '.join(missing_files)}")

    if not missing:
        sys.path.insert(0, str(ROOT))
        for name in BACKEND_MODULES:
            try:
                importlib.import_module(name)
                if verbose:
                    print(f"[ok]   {name}")
            except Exception as exc:  # import hatasını açıkça göster
                ok = False
                print(f"[HATA] {name}: {type(exc).__name__}: {exc}")
    return ok


def run_tests(extra):
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *extra], cwd=ROOT)


def run_report():
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "run_test_report.py")], cwd=ROOT)


def kill_server(port):
    """Belirtilen portta dinleyen sunucu süreçlerini durdur."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True
        )
    except FileNotFoundError:
        print("[HATA] 'lsof' bulunamadı; süreç otomatik durdurulamadı.")
        return 1
    pids = result.stdout.strip().splitlines()
    if not pids:
        print(f"[bilgi] Port {port}'da çalışan süreç bulunamadı.")
        return 0
    killed = []
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError, ValueError) as e:
            print(f"[uyarı] PID {pid} durdurulamadı: {e}")
    if killed:
        print(f"[ok] Sunucu durduruldu (PID: {', '.join(killed)}).")
    return 0


def serve(host, port, reload, open_browser):
    import uvicorn

    base = f"http://{host}:{port}/"
    print("\nFSO Fizik Motoru başlıyor…")
    for label, path in PANELS:
        print(f"  • {label:45s} {base}{path}")
    print("  Not: 3B görünüm three.js'i CDN'den yükler (internet bağlantısı gerekir).")
    print("  Durdurmak için Ctrl+C\n")

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(base)).start()

    os.chdir(ROOT)
    # include/exclude filtreleri yalnızca watchfiles kuruluysa çalışır; yoksa uvicorn .py dosyalarını izler.
    use_filters = reload and importlib.util.find_spec("watchfiles") is not None
    uvicorn.run(
        "backend.app:app",
        host=host,
        port=port,
        reload=reload,
        # Yalnızca kaynak dosyaları izle; .venv, raporlar ve önbellekler yeniden yüklemeyi tetiklemesin.
        reload_dirs=[str(ROOT)] if reload else None,
        reload_includes=["*.py", "*.js", "*.html", "*.css"] if use_filters else None,
        reload_excludes=[".venv/*", "reports/*", "tests/*", "**/__pycache__/*"] if use_filters else None,
    )


def main():
    parser = argparse.ArgumentParser(description="FSO Fizik Motoru başlatıcı")
    parser.add_argument("--host", default=os.environ.get("FSO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FSO_PORT", "8000")))
    parser.add_argument("--no-reload", action="store_true", help="otomatik yeniden yüklemeyi kapat")
    parser.add_argument("--open", action="store_true", help="tarayıcıda aç")
    parser.add_argument("--check", action="store_true", help="yalnızca ortam/modül kontrolü")
    parser.add_argument("--test", action="store_true", help="pytest çalıştır")
    parser.add_argument("--report", action="store_true", help="test raporu ve grafikleri üret")
    parser.add_argument("--exit", action="store_true", help="çalışan sunucuyu durdur")
    args, extra = parser.parse_known_args()

    if args.check:
        sys.exit(0 if check_environment() else 1)
    if args.test:
        sys.exit(run_tests(extra))
    if args.report:
        sys.exit(run_report())
    if args.exit:
        sys.exit(kill_server(args.port))

    if not check_environment(verbose=False):
        sys.exit(1)
    serve(args.host, args.port, not args.no_reload, args.open)


if __name__ == "__main__":
    main()
