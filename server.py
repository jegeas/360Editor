"""Editor de panorámicas 360 con IA (Gemini).

Flujo:
  1. Se elige una panorámica equirectangular y se navega por ella.
  2. La vista actual se reproyecta a una imagen plana (rectilínea) sin distorsión.
  3. Se pinta una máscara y Gemini quita o añade elementos en esa zona.
  4. El resultado se fusiona solo dentro de la máscara y se reproyecta de vuelta
     a la panorámica a resolución completa.

Arranque:  python server.py   ->  http://localhost:8361

El navegador abre la foto del ordenador del usuario y la sube a este servidor
local; al guardar, el navegador escribe la panorámica editada donde el usuario
elija (sobrescribiendo el archivo o con otro nombre).
"""

import base64
import io
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"
PORT = int(os.environ.get("EDITOR_PORT", "8361"))
DEFAULT_MODEL = "gemini-3.1-flash-image"
VIEW_WIDTH = 4096          # resolución de la panorámica que ve el navegador
MAX_UNDO = 15
MAX_UPLOAD = 400 * 1024 * 1024


# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

def load_config():
    cfg = {"api_key": "", "model": DEFAULT_MODEL}
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    if os.environ.get("GEMINI_API_KEY"):
        cfg["api_key"] = os.environ["GEMINI_API_KEY"]
    return cfg


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Geometría equirectangular <-> perspectiva
#   Convención de Pannellum: yaw 0 = centro de la imagen, yaw + = derecha,
#   pitch + = arriba.  Mundo: z adelante, x derecha, y arriba.
# --------------------------------------------------------------------------

def sample(img, x, y, wrap_x):
    """Muestreo bilineal. img: HxWxC float32/uint8. Devuelve float32."""
    h, w = img.shape[:2]
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0)[..., None].astype(np.float32)
    fy = (y - y0)[..., None].astype(np.float32)
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)
    if wrap_x:
        x1 = (x0 + 1) % w
        x0 = x0 % w
    else:
        x1 = np.clip(x0 + 1, 0, w - 1)
        x0 = np.clip(x0, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    y0 = np.clip(y0, 0, h - 1)
    a = img[y0, x0].astype(np.float32)
    b = img[y0, x1].astype(np.float32)
    c = img[y1, x0].astype(np.float32)
    d = img[y1, x1].astype(np.float32)
    top = a + (b - a) * fx
    bot = c + (d - c) * fx
    return top + (bot - top) * fy


def focal(n, fov):
    return (n / 2.0) / math.tan(fov / 2.0)


def render_view(eq, yaw, pitch, fov, n):
    """Equirectangular -> vista rectilínea cuadrada n x n."""
    H, W = eq.shape[:2]
    f = focal(n, fov)
    t = np.arange(n, dtype=np.float32) - n / 2.0 + 0.5
    X = np.broadcast_to(t[None, :], (n, n))
    Y = np.broadcast_to(-t[:, None], (n, n))
    Z = np.full((n, n), f, dtype=np.float32)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    y1 = Y * cp + Z * sp
    z1 = -Y * sp + Z * cp
    x2 = X * cy + z1 * sy
    z2 = -X * sy + z1 * cy
    lon = np.arctan2(x2, z2)
    lat = np.arctan2(y1, np.sqrt(x2 * x2 + z2 * z2))
    u = (lon / (2 * math.pi) + 0.5) * W - 0.5
    v = (0.5 - lat / math.pi) * H - 0.5
    return np.clip(sample(eq, u, v, True), 0, 255).astype(np.uint8)


def view_dirs_to_lonlat(px, py, yaw, pitch, fov, n):
    f = focal(n, fov)
    X = px - n / 2.0 + 0.5
    Y = -(py - n / 2.0 + 0.5)
    Z = np.full_like(X, f)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    y1 = Y * cp + Z * sp
    z1 = -Y * sp + Z * cp
    x2 = X * cy + z1 * sy
    z2 = -X * sy + z1 * cy
    return np.arctan2(x2, z2), np.arctan2(y1, np.sqrt(x2 * x2 + z2 * z2))


def wrap_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def project_back(eq, view, alpha, yaw, pitch, fov):
    """Fusiona `view` (n x n) en `eq` donde alpha > 0. Devuelve datos para deshacer."""
    H, W = eq.shape[:2]
    n = view.shape[0]
    f = focal(n, fov)

    # Huella de la máscara sobre la panorámica
    ys, xs = np.nonzero(alpha > 0.002)
    if len(xs) == 0:
        return None
    step = max(1, len(xs) // 40000)
    lon, lat = view_dirs_to_lonlat(xs[::step].astype(np.float32), ys[::step].astype(np.float32),
                                   yaw, pitch, fov, n)
    margin = 4
    vmin = max(0, int((0.5 - lat.max() / math.pi) * H) - margin)
    vmax = min(H - 1, int((0.5 - lat.min() / math.pi) * H) + margin)
    dl = wrap_angle(lon - yaw)
    full_width = lat.max() > math.radians(80) or lat.min() < math.radians(-80) or (dl.max() - dl.min()) > math.radians(300)
    if full_width:
        cols = np.arange(W)
        vmin = 0 if lat.max() > math.radians(80) else vmin
        vmax = H - 1 if lat.min() < math.radians(-80) else vmax
    else:
        umin = int(math.floor(((yaw + dl.min()) / (2 * math.pi) + 0.5) * W)) - margin
        umax = int(math.ceil(((yaw + dl.max()) / (2 * math.pi) + 0.5) * W)) + margin
        cols = np.arange(umin, umax + 1) % W

    rows = np.arange(vmin, vmax + 1)
    undo = (rows, cols, eq[np.ix_(rows, cols)].copy())

    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    lon_c = (((cols + 0.5) / W) - 0.5) * 2 * math.pi
    sin_lon = np.sin(lon_c).astype(np.float32)[None, :]
    cos_lon = np.cos(lon_c).astype(np.float32)[None, :]
    alpha3 = alpha[..., None]
    chunk = max(1, 4_000_000 // max(1, len(cols)))
    for r0 in range(0, len(rows), chunk):
        rr = rows[r0:r0 + chunk]
        lat_r = ((0.5 - (rr + 0.5) / H) * math.pi).astype(np.float32)[:, None]
        cl, sl = np.cos(lat_r), np.sin(lat_r)
        x = cl * sin_lon
        y = np.broadcast_to(sl, x.shape)
        z = cl * cos_lon
        # yaw inverso
        x1 = x * cy - z * sy
        z1 = x * sy + z * cy
        # pitch inverso
        Y = y * cp - z1 * sp
        Z = y * sp + z1 * cp
        valid = Z > 1e-6
        Zs = np.where(valid, Z, 1.0)
        px = f * x1 / Zs + n / 2.0 - 0.5
        py = -f * Y / Zs + n / 2.0 - 0.5
        valid &= (px >= 0) & (px <= n - 1) & (py >= 0) & (py <= n - 1)
        if not valid.any():
            continue
        a = sample(alpha3, px, py, False)
        a[~valid] = 0
        e = sample(view, px, py, False)
        orig = eq[np.ix_(rr, cols)].astype(np.float32)
        out = orig + (e - orig) * a
        eq[np.ix_(rr, cols)] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    return undo


# --------------------------------------------------------------------------
# Máscara y fusión
# --------------------------------------------------------------------------

def mask_from_png(data_url, n):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    m = Image.open(io.BytesIO(raw)).convert("RGBA")
    m = m.getchannel("A").resize((n, n), Image.BILINEAR)
    return m.point(lambda p: 255 if p > 20 else 0)


def feather(mask_l, n, grow):
    """Máscara binaria -> alpha suave (float 0..1)."""
    m = mask_l
    if grow:
        k = max(3, (n // 128) | 1)
        m = m.filter(ImageFilter.MaxFilter(k))
    m = m.filter(ImageFilter.GaussianBlur(max(1.5, n / 220)))
    return np.asarray(m, dtype=np.float32) / 255.0


def color_match(result, original, alpha):
    """Corrige la deriva global de color de la IA usando la zona no editada."""
    keep = alpha < 0.01
    if keep.sum() < 500:
        return result
    out = result.astype(np.float32)
    for c in range(3):
        r = out[..., c][keep]
        o = original[..., c][keep].astype(np.float32)
        sr, so = r.std() + 1e-3, o.std() + 1e-3
        gain = float(np.clip(so / sr, 0.8, 1.25))
        out[..., c] = (out[..., c] - r.mean()) * gain + o.mean()
    return np.clip(out, 0, 255)


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def to_b64(img, fmt="JPEG"):
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, "JPEG", quality=93)
    else:
        img.save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_prompt(mode, text):
    common = (
        "You are editing one flat perspective view taken from a 360-degree photograph of a real "
        "interior/exterior. The FIRST image is the photo. The SECOND image is a mask: the WHITE area "
        "is the only region you may change; BLACK must stay pixel-identical. "
        "Return the full image with exactly the same framing, size, camera position, perspective, "
        "lens and composition. Do not crop, zoom, shift, recolor or restyle anything. "
    )
    if mode == "remove":
        what = text.strip() or "the object(s)"
        return common + (
            f"Task: completely remove {what} inside the white area. Fill that area with what would "
            "realistically be behind it (walls, floor, stone, wood, sky...), continuing the surrounding "
            "textures, lines, lighting and shadows seamlessly. Remove any shadows or reflections the "
            "object casts. Do not add any new object."
        )
    return common + (
        f"Task: add {text.strip()} inside the white area. It must look like it was really photographed "
        "in this place: correct scale and perspective for this camera, matching lighting direction, "
        "color temperature, grain, sharpness, and realistic contact shadows on the surface it rests on."
    )


def call_gemini(cfg, photo, mask, prompt):
    key = cfg.get("api_key")
    if not key:
        raise RuntimeError("Falta la API key de Gemini. Ponla en Ajustes.")
    model = cfg.get("model") or DEFAULT_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": to_b64(photo)}},
            {"inline_data": {"mime_type": "image/png", "data": to_b64(mask, "PNG")}},
        ]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": "1:1", "imageSize": "2K"},
        },
    }

    def post(payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=240) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        data = post(body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if e.code == 400 and "imageConfig" in json.dumps(body) and ("image" in detail.lower() or "config" in detail.lower()):
            # Modelos que no aceptan imageSize: reintentar sin esa opción
            body["generationConfig"].pop("imageConfig", None)
            try:
                data = post(body)
            except urllib.error.HTTPError as e2:
                raise RuntimeError(gemini_error(e2.code, e2.read().decode("utf-8", "replace")))
        else:
            raise RuntimeError(gemini_error(e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError(f"No se pudo conectar con Gemini: {e.reason}")

    texts = []
    for cand in data.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return Image.open(io.BytesIO(base64.b64decode(inline["data"]))).convert("RGB")
            if part.get("text"):
                texts.append(part["text"])
    reason = ""
    if data.get("candidates"):
        reason = data["candidates"][0].get("finishReason", "")
    if data.get("promptFeedback", {}).get("blockReason"):
        reason = data["promptFeedback"]["blockReason"]
    raise RuntimeError("Gemini no devolvió imagen. " + (reason + ". " if reason else "") + " ".join(texts)[:400])


def gemini_error(code, detail):
    try:
        msg = json.loads(detail)["error"]["message"]
    except Exception:
        msg = detail[:300]
    return f"Error de Gemini ({code}): {msg}"


# --------------------------------------------------------------------------
# Estado de la sesión de edición (una foto abierta cada vez)
# --------------------------------------------------------------------------

class Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.name = None        # nombre del archivo abierto
        self.eq = None          # np.uint8 HxWx3, versión de trabajo
        self.meta = {}          # exif / icc_profile / xmp del original (GPano, cámara...)
        self.undo = []
        self.version = 0
        self.saved_version = 0
        self.view_cache = None
        self.results = {}       # id -> dict(view, alpha, yaw, pitch, fov)

    @property
    def dirty(self):
        return self.eq is not None and self.version != self.saved_version

    def load(self, name, data):
        img = Image.open(io.BytesIO(data))
        meta = {k: img.info[k] for k in ("exif", "icc_profile", "xmp") if img.info.get(k)}
        eq = np.array(img.convert("RGB"))
        with self.lock:
            self.name = name
            self.eq = eq
            self.meta = meta
            self.undo = []
            self.results = {}
            self.version += 1
            self.saved_version = self.version
            self.view_cache = None

    def export(self, fmt):
        """Panorámica a resolución completa, conservando los metadatos del original."""
        with self.lock:
            img = Image.fromarray(self.eq)
            meta = dict(self.meta)
            version = self.version
        buf = io.BytesIO()
        if fmt == "png":
            kw = {k: meta[k] for k in ("icc_profile", "exif") if k in meta}
            img.save(buf, "PNG", **kw)
            mime = "image/png"
        else:
            kw = {k: meta[k] for k in ("icc_profile", "exif", "xmp") if k in meta}
            try:
                img.save(buf, "JPEG", quality=95, subsampling=0, **kw)
            except TypeError:       # Pillow antiguo sin soporte de xmp=
                kw.pop("xmp", None)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=95, subsampling=0, **kw)
            mime = "image/jpeg"
        return buf.getvalue(), mime, version

    def view_jpeg(self):
        with self.lock:
            if self.view_cache and self.view_cache[0] == self.version:
                return self.view_cache[1]
            img = Image.fromarray(self.eq)
            w = min(VIEW_WIDTH, img.width)
            img = img.resize((w, w // 2), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88)
            self.view_cache = (self.version, buf.getvalue())
            return self.view_cache[1]


S = Session()


def crop_size(fov):
    native = S.eq.shape[1] * fov / (2 * math.pi)
    return int(np.clip(round(native / 64) * 64, 1024, 2048))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[editor] " + (fmt % args) + "\n")

    def send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def raw_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > MAX_UPLOAD:
            raise ValueError("Archivo demasiado grande")
        return self.rfile.read(n) if n else b""

    def body(self):
        data = self.raw_body()
        return json.loads(data.decode("utf-8")) if data else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                cfg = load_config()
                return self.send(200, {
                    "name": S.name, "version": S.version, "undo": len(S.undo), "dirty": S.dirty,
                    "hasKey": bool(cfg.get("api_key")), "model": cfg.get("model") or DEFAULT_MODEL,
                    "size": list(S.eq.shape[1::-1]) if S.eq is not None else None,
                })
            if S.eq is None and u.path.startswith("/api/"):
                return self.send(404, {"error": "No hay ninguna foto abierta"})
            if u.path == "/api/view.jpg":
                return self.send(200, S.view_jpeg(), "image/jpeg")
            if u.path == "/api/crop":
                yaw, pitch, fov = (math.radians(float(q[k])) for k in ("yaw", "pitch", "fov"))
                fov = float(np.clip(fov, math.radians(15), math.radians(110)))
                with S.lock:
                    n = crop_size(fov)
                    view = render_view(S.eq, yaw, pitch, fov, n)
                buf = io.BytesIO()
                Image.fromarray(view).save(buf, "JPEG", quality=92)
                return self.send(200, buf.getvalue(), "image/jpeg")
            if u.path == "/api/export":
                data, mime, version = S.export("png" if q.get("fmt") == "png" else "jpeg")
                return self.send(200, data, mime, {"X-Version": str(version)})
            return self.send(404, {"error": "No encontrado"})
        except Exception as e:
            return self.send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/upload":
                name = unquote(self.headers.get("X-Filename", "") or "panoramica.jpg")
                data = self.raw_body()
                if not data:
                    return self.send(400, {"error": "Archivo vacío"})
                try:
                    S.load(Path(name).name, data)
                except Exception:
                    return self.send(400, {"error": "No se pudo leer la imagen. Usa un JPG o PNG."})
                w, h = S.eq.shape[1], S.eq.shape[0]
                warning = None
                if abs(w / h - 2) > 0.03:
                    warning = (f"La foto mide {w}×{h} px y no tiene proporción 2:1. "
                               "Puede que no sea una panorámica 360 equirectangular y se vea deformada.")
                return self.send(200, {"ok": True, "version": S.version, "warning": warning})

            b = self.body()
            if u.path == "/api/config":
                cfg = load_config()
                if b.get("api_key"):
                    cfg["api_key"] = b["api_key"].strip()
                if b.get("model"):
                    cfg["model"] = b["model"].strip()
                save_config(cfg)
                return self.send(200, {"ok": True})

            if S.eq is None:
                return self.send(400, {"error": "Abre una foto primero"})

            if u.path == "/api/saved":
                with S.lock:
                    S.saved_version = int(b.get("version", S.version))
                    if b.get("name"):
                        S.name = Path(b["name"]).name
                return self.send(200, {"ok": True})

            if u.path == "/api/edit":
                mode = b.get("mode", "remove")
                text = b.get("prompt", "")
                if mode == "add" and not text.strip():
                    return self.send(400, {"error": "Describe qué quieres añadir"})
                yaw, pitch, fov = (math.radians(float(b[k])) for k in ("yaw", "pitch", "fov"))
                fov = float(np.clip(fov, math.radians(15), math.radians(110)))
                with S.lock:
                    n = crop_size(fov)
                    view = render_view(S.eq, yaw, pitch, fov, n)
                mask = mask_from_png(b["mask"], n)
                if not np.asarray(mask).any():
                    return self.send(400, {"error": "Pinta la zona a editar"})
                t0 = time.time()
                ai = call_gemini(load_config(), Image.fromarray(view), mask, build_prompt(mode, text))
                ai = np.asarray(ai.resize((n, n), Image.LANCZOS), dtype=np.float32)
                alpha = feather(mask, n, grow=(mode == "remove"))
                ai = color_match(ai, view, alpha)
                comp = view.astype(np.float32) + (ai - view.astype(np.float32)) * alpha[..., None]
                comp = np.clip(comp + 0.5, 0, 255).astype(np.uint8)
                rid = uuid.uuid4().hex[:10]
                with S.lock:
                    S.results = {rid: dict(view=comp, alpha=alpha, yaw=yaw, pitch=pitch, fov=fov,
                                           version=S.version)}
                buf = io.BytesIO()
                Image.fromarray(comp).save(buf, "JPEG", quality=92)
                return self.send(200, {"id": rid, "seconds": round(time.time() - t0, 1),
                                       "image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()})

            if u.path == "/api/apply":
                with S.lock:
                    r = S.results.get(b.get("id"))
                    if not r:
                        return self.send(400, {"error": "Resultado caducado, vuelve a generar"})
                    if r["version"] != S.version:
                        return self.send(409, {"error": "La panorámica cambió desde que se generó"})
                    undo = project_back(S.eq, r["view"], r["alpha"], r["yaw"], r["pitch"], r["fov"])
                    if undo:
                        S.undo.append(undo)
                        S.undo = S.undo[-MAX_UNDO:]
                    S.results = {}
                    S.version += 1
                return self.send(200, {"ok": True, "version": S.version})

            if u.path == "/api/undo":
                with S.lock:
                    if not S.undo:
                        return self.send(400, {"error": "Nada que deshacer"})
                    rows, cols, patch = S.undo.pop()
                    S.eq[np.ix_(rows, cols)] = patch
                    S.version += 1
                return self.send(200, {"ok": True, "version": S.version})

            return self.send(404, {"error": "No encontrado"})
        except RuntimeError as e:
            return self.send(502, {"error": str(e)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})


class Server(ThreadingHTTPServer):
    # En Windows, SO_REUSEADDR deja que varios procesos escuchen en el mismo puerto y
    # responde el más antiguo. Se pide uso exclusivo para que un segundo arranque falle.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def server_bind(self):
        import socket
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"El puerto {PORT} ya está en uso: probablemente el editor ya está abierto.")
        print(f"Ábrelo en http://localhost:{PORT} o cierra la otra ventana del editor.")
        sys.exit(1)
    print(f"Editor 360 en http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
