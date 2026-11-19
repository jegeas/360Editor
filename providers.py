"""Proveedores de IA para quitar o añadir elementos en una vista.

Todos reciben lo mismo: la vista plana (PIL RGB, n x n), una máscara (PIL "L",
blanco = zona a editar), el modo ("remove" | "add") y el texto del usuario.
Devuelven una imagen PIL RGB; el servidor la reescala y solo usa lo que queda
dentro de la máscara.

Cada persona usa sus propias claves, que se guardan en config.json (fuera de git)
o se leen de variables de entorno.
"""

import base64
import hashlib
import io
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid


import numpy as np
from PIL import Image

from paths import FROZEN, MODELS_DIR


class ProviderError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Utilidades HTTP (solo biblioteca estándar)
# --------------------------------------------------------------------------

def img_bytes(img, fmt="PNG", **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def b64(data):
    return base64.b64encode(data).decode("ascii")


def multipart(fields, files):
    """fields: {nombre: texto}; files: {nombre: (archivo, bytes, mime)}."""
    boundary = "----editor360" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, data, mime) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                  f"Content-Type: {mime}\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def http(method, url, headers=None, data=None, timeout=240):
    """Devuelve (código, cabeceras, cuerpo). No lanza excepción por códigos HTTP."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except urllib.error.URLError as e:
        raise ProviderError(f"No se pudo conectar: {e.reason}")


def error_text(body):
    try:
        d = json.loads(body)
    except Exception:
        return body[:300].decode("utf-8", "replace") if isinstance(body, bytes) else str(body)[:300]
    if isinstance(d.get("error"), dict):
        return d["error"].get("message") or json.dumps(d["error"])[:300]
    for k in ("errors", "detail", "message", "error", "name"):
        if d.get(k):
            v = d[k]
            return "; ".join(map(str, v)) if isinstance(v, list) else str(v)
    return json.dumps(d)[:300]


def common_error(label, code, msg, billing_url):
    if code in (401, 403):
        return f"{label} ha rechazado tu API key. Cámbiala en Ajustes."
    low = msg.lower()
    if code == 402 or "billing" in low or "credit" in low or "prepayment" in low or "insufficient" in low:
        return f"Tu cuenta de {label} no tiene saldo o facturación activa. Revísalo en {billing_url} ({msg})"
    if code == 429:
        return f"Has superado el límite de uso de {label}. Espera un poco y vuelve a intentarlo. ({msg})"
    return f"Error de {label} ({code}): {msg}"


# --------------------------------------------------------------------------
# Instrucciones para la IA
# --------------------------------------------------------------------------

def instruction(mode, text, mask_desc):
    """Para modelos que siguen instrucciones (Gemini, OpenAI)."""
    common = (
        "You are editing one flat perspective view taken from a 360-degree photograph of a real "
        f"place. {mask_desc} Return the full image with exactly the same framing, size, camera "
        "position, perspective, lens and composition. Do not crop, zoom, shift, recolor or restyle "
        "anything outside the editable area. "
    )
    if mode == "integrate":
        what = f" ({text.strip()})" if text.strip() else ""
        return common + (
            f"Task: the editable area contains an object{what} that was pasted onto the photo. Make it look "
            "like it was really photographed in this place: adapt its lighting, shading, color temperature, "
            "sharpness, noise and grain to the scene, and add realistic contact shadows and reflections on "
            "the nearby surfaces. Keep the object exactly where it is, with the same size, outline, design, "
            "colors, text and details. Do not replace it with a different object and do not change anything "
            "else."
        )
    if mode == "remove":
        what = text.strip() or "the object(s)"
        return common + (
            f"Task: completely remove {what} inside the editable area. Fill it with what would "
            "realistically be behind it (walls, floor, stone, wood, sky...), continuing the surrounding "
            "textures, lines, lighting and shadows seamlessly. Remove any shadows or reflections the "
            "object casts. Do not add any new object."
        )
    return common + (
        f"Task: add {text.strip()} inside the editable area. It must look like it was really "
        "photographed in this place: correct scale and perspective for this camera, matching lighting "
        "direction, color temperature, grain, sharpness, and realistic contact shadows."
    )


def fill_prompt(mode, text):
    """Para modelos de relleno (FLUX, Stability): describen el contenido, no la tarea."""
    if mode == "remove":
        return ("empty background seamlessly continuing the surrounding surfaces, same texture, "
                "lighting and perspective, no objects, photorealistic")
    return f"{text.strip()}, photorealistic, natural lighting matching the scene, realistic shadows"


# --------------------------------------------------------------------------
# Proveedores
# --------------------------------------------------------------------------

class Provider:
    id = ""
    label = ""
    needs_key = True
    can_add = True
    can_integrate = False   # sabe integrar una imagen pegada (luz y sombras) sin redibujarla
    env = ""                # variable de entorno con la clave
    key_url = ""            # dónde se consigue la clave
    billing_url = ""
    default_model = ""
    models = []
    note = ""               # explicación corta para el usuario

    def describe(self):
        return {"id": self.id, "label": self.label, "needsKey": self.needs_key, "canAdd": self.can_add,
                "canIntegrate": self.can_integrate,
                "keyUrl": self.key_url, "env": self.env, "defaultModel": self.default_model,
                "models": self.models, "note": self.note}

    def check_key(self, key, model):
        raise NotImplementedError

    def edit(self, key, model, photo, mask, mode, text):
        raise NotImplementedError


class Gemini(Provider):
    id, label = "gemini", "Google Gemini"
    can_integrate = True
    env = "GEMINI_API_KEY"
    key_url = "https://aistudio.google.com/apikey"
    billing_url = "https://ai.studio/projects"
    default_model = "gemini-3.1-flash-image"
    models = ["gemini-3.1-flash-image", "gemini-3-pro-image-preview", "gemini-2.5-flash-image"]
    note = "Quita y añade. Entiende instrucciones en español."

    def check_key(self, key, model):
        code, _, body = http("GET", f"https://generativelanguage.googleapis.com/v1beta/models/{model}",
                             {"x-goog-api-key": key}, timeout=20)
        if code == 200:
            return None
        if code == 404:
            return f"La clave es válida, pero el modelo «{model}» no existe o no está disponible para ella."
        if code in (400, 401, 403):
            return "Google ha rechazado la clave. Revisa que la has copiado entera."
        return common_error(self.label, code, error_text(body), self.billing_url)

    def edit(self, key, model, photo, mask, mode, text):
        prompt = instruction(mode, text, "The FIRST image is the photo. The SECOND image is a mask: the "
                             "WHITE area is the only region you may change; BLACK must stay identical.")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64(img_bytes(photo, "JPEG", quality=93))}},
                {"inline_data": {"mime_type": "image/png", "data": b64(img_bytes(mask))}},
            ]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": "1:1", "imageSize": "2K"}},
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        code, _, raw = http("POST", url, headers, json.dumps(body).encode())
        if code == 400 and b"image" in raw.lower():
            # Modelos que no aceptan imageSize: reintentar sin esa opción
            body["generationConfig"].pop("imageConfig")
            code, _, raw = http("POST", url, headers, json.dumps(body).encode())
        if code != 200:
            if b"API_KEY_INVALID" in raw or b"API key not valid" in raw:
                code = 401
            raise ProviderError(common_error(self.label, code, error_text(raw), self.billing_url))
        data = json.loads(raw)
        texts = []
        for cand in data.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return Image.open(io.BytesIO(base64.b64decode(inline["data"]))).convert("RGB")
                if part.get("text"):
                    texts.append(part["text"])
        reason = (data.get("promptFeedback") or {}).get("blockReason") or \
            (data.get("candidates") or [{}])[0].get("finishReason", "")
        raise ProviderError("Gemini no devolvió imagen. " + (reason + ". " if reason else "") + " ".join(texts)[:300])


class OpenAI(Provider):
    id, label = "openai", "OpenAI"
    can_integrate = True
    env = "OPENAI_API_KEY"
    key_url = "https://platform.openai.com/api-keys"
    billing_url = "https://platform.openai.com/settings/organization/billing"
    default_model = "gpt-image-2"
    models = ["gpt-image-2", "gpt-image-2.6-flare", "gpt-image-2.6-sunburst", "gpt-image-1.5"]
    note = "Quita y añade con máscara. Muy bueno añadiendo objetos."

    def check_key(self, key, model):
        code, _, body = http("GET", f"https://api.openai.com/v1/models/{model}",
                             {"Authorization": f"Bearer {key}"}, timeout=20)
        if code == 200:
            return None
        if code == 404:
            return f"La clave es válida, pero el modelo «{model}» no existe o tu cuenta no tiene acceso."
        if code == 401:
            return "OpenAI ha rechazado la clave. Revisa que la has copiado entera."
        return common_error(self.label, code, error_text(body), self.billing_url)

    def edit(self, key, model, photo, mask, mode, text):
        size = 1024
        img = photo.resize((size, size), Image.LANCZOS)
        # OpenAI: la zona transparente (alfa 0) es la que se edita
        m = mask.resize((size, size), Image.NEAREST)
        rgba = img.convert("RGBA")
        rgba.putalpha(m.point(lambda p: 0 if p > 127 else 255))
        prompt = instruction(mode, text, "The transparent area of the mask marks the only region "
                             "you may change.")
        fields = {"model": model, "prompt": prompt, "size": f"{size}x{size}", "quality": "high",
                  "n": "1", "output_format": "png"}
        if model.startswith("gpt-image-1.5"):
            fields["input_fidelity"] = "high"
        data, ctype = multipart(fields, {"image": ("vista.png", img_bytes(img), "image/png"),
                                         "mask": ("mascara.png", img_bytes(rgba), "image/png")})
        code, _, raw = http("POST", "https://api.openai.com/v1/images/edits",
                            {"Authorization": f"Bearer {key}", "Content-Type": ctype}, data, timeout=300)
        if code != 200:
            raise ProviderError(common_error(self.label, code, error_text(raw), self.billing_url))
        items = json.loads(raw).get("data") or []
        if not items or not items[0].get("b64_json"):
            raise ProviderError("OpenAI no devolvió imagen.")
        return Image.open(io.BytesIO(base64.b64decode(items[0]["b64_json"]))).convert("RGB")


class Stability(Provider):
    id, label = "stability", "Stability AI"
    env = "STABILITY_API_KEY"
    key_url = "https://platform.stability.ai/account/keys"
    billing_url = "https://platform.stability.ai/account/credits"
    default_model = ""
    models = []
    note = "Quitar usa «Erase» (sin texto). Añadir usa «Inpaint»: escribe en inglés para mejores resultados."

    def check_key(self, key, model):
        code, _, body = http("GET", "https://api.stability.ai/v1/user/account",
                             {"Authorization": f"Bearer {key}"}, timeout=20)
        if code == 200:
            return None
        if code in (401, 403):
            return "Stability AI ha rechazado la clave. Revisa que la has copiado entera."
        return common_error(self.label, code, error_text(body), self.billing_url)

    def edit(self, key, model, photo, mask, mode, text):
        # Stability: blanco = zona a editar
        files = {"image": ("vista.png", img_bytes(photo), "image/png"),
                 "mask": ("mascara.png", img_bytes(mask), "image/png")}
        fields = {"output_format": "png", "grow_mask": "6"}
        if mode == "remove":
            url = "https://api.stability.ai/v2beta/stable-image/edit/erase"
        else:
            url = "https://api.stability.ai/v2beta/stable-image/edit/inpaint"
            fields["prompt"] = fill_prompt(mode, text)
        data, ctype = multipart(fields, files)
        code, headers, raw = http("POST", url, {"Authorization": f"Bearer {key}", "Content-Type": ctype,
                                                "Accept": "image/*"}, data, timeout=300)
        if code != 200 or not headers.get("Content-Type", "").startswith("image/"):
            raise ProviderError(common_error(self.label, code, error_text(raw), self.billing_url))
        return Image.open(io.BytesIO(raw)).convert("RGB")


class FluxReplicate(Provider):
    id, label = "replicate", "FLUX Fill (Replicate)"
    env = "REPLICATE_API_TOKEN"
    key_url = "https://replicate.com/account/api-tokens"
    billing_url = "https://replicate.com/account/billing"
    default_model = "black-forest-labs/flux-fill-pro"
    models = ["black-forest-labs/flux-fill-pro", "black-forest-labs/flux-fill-dev"]
    note = "Modelo abierto FLUX. Para añadir, escribe en inglés para mejores resultados."

    def check_key(self, key, model):
        code, _, body = http("GET", "https://api.replicate.com/v1/account",
                             {"Authorization": f"Bearer {key}"}, timeout=20)
        if code != 200:
            if code in (401, 403):
                return "Replicate ha rechazado el token. Revisa que lo has copiado entero."
            return common_error(self.label, code, error_text(body), self.billing_url)
        code, _, body = http("GET", f"https://api.replicate.com/v1/models/{model}",
                             {"Authorization": f"Bearer {key}"}, timeout=20)
        if code == 404:
            return f"El token es válido, pero el modelo «{model}» no existe en Replicate."
        return None

    def edit(self, key, model, photo, mask, mode, text):
        auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {"input": {
            "image": "data:image/png;base64," + b64(img_bytes(photo)),
            "mask": "data:image/png;base64," + b64(img_bytes(mask)),   # blanco = rellenar
            "prompt": fill_prompt(mode, text),
            "output_format": "png",
        }}
        code, _, raw = http("POST", f"https://api.replicate.com/v1/models/{model}/predictions",
                            {**auth, "Prefer": "wait=60"}, json.dumps(body).encode(), timeout=120)
        if code not in (200, 201):
            raise ProviderError(common_error(self.label, code, error_text(raw), self.billing_url))
        pred = json.loads(raw)
        deadline = time.time() + 240
        while pred.get("status") in ("starting", "processing") and time.time() < deadline:
            time.sleep(2)
            code, _, raw = http("GET", pred["urls"]["get"], auth, timeout=30)
            pred = json.loads(raw)
        if pred.get("status") != "succeeded":
            raise ProviderError(f"FLUX no terminó la imagen ({pred.get('status')}): {pred.get('error') or ''}")
        out = pred.get("output")
        url = out[0] if isinstance(out, list) else out
        code, _, img = http("GET", url, timeout=120)
        if code != 200:
            raise ProviderError("No se pudo descargar el resultado de Replicate.")
        return Image.open(io.BytesIO(img)).convert("RGB")


# --------------------------------------------------------------------------
# Borrado local con LaMa (sin clave, sin internet una vez descargado)
# --------------------------------------------------------------------------

LAMA_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
LAMA_FILE = MODELS_DIR / "lama_fp32.onnx"
# Huella y tamaño publicados por Hugging Face (X-Linked-ETag / X-Linked-Size). Si el archivo
# descargado no coincide exactamente, se descarta: así solo se usa este modelo concreto.
LAMA_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
LAMA_BYTES = 208044816
LAMA_SIZE_MB = round(LAMA_BYTES / 1e6)
RUNTIME_SIZE_MB = 15


class LocalLama(Provider):
    id, label = "local", "Local (LaMa, sin clave)"
    needs_key = False
    can_add = False
    note = "Gratis y sin internet tras la descarga. Solo quita elementos; no puede añadir."

    def __init__(self):
        self.lock = threading.Lock()          # instalación
        self.load_lock = threading.Lock()     # carga del modelo en memoria
        self.session = None
        self.loading = False
        self.job = {"running": False, "step": "", "done": 0, "total": 0, "error": None}

    # --- instalación ---------------------------------------------------
    @staticmethod
    def runtime_ok():
        try:
            import onnxruntime  # noqa: F401
            return True
        except Exception:
            return False

    def status(self):
        return {"runtime": self.runtime_ok(), "model": LAMA_FILE.exists(),
                "ready": self.runtime_ok() and LAMA_FILE.exists(),
                "loaded": self.session is not None, "loading": self.loading,
                "runtimeMB": RUNTIME_SIZE_MB, "modelMB": LAMA_SIZE_MB, "job": dict(self.job)}

    def install(self):
        with self.lock:
            if self.job["running"]:
                return
            self.job = {"running": True, "step": "Preparando…", "done": 0, "total": 0, "error": None}
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self):
        try:
            if not self.runtime_ok():
                if FROZEN:      # en el ejecutable ya viene incluido; pip no está disponible
                    raise RuntimeError("Falta onnxruntime en esta instalación. Reinstala el Editor 360.")
                self.job["step"] = "Instalando onnxruntime…"
                r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                    "onnxruntime"], capture_output=True, text=True)
                if r.returncode != 0 or not self.runtime_ok():
                    raise RuntimeError("No se pudo instalar onnxruntime: " + (r.stderr or r.stdout)[-300:])
            if not LAMA_FILE.exists():
                self._download()
            self.job.update(step="Listo", running=False)
        except Exception as e:
            self.job.update(running=False, error=str(e), step="Error")

    def _download(self):
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        part = LAMA_FILE.with_suffix(".part")
        self.job["step"] = "Descargando el modelo LaMa…"
        req = urllib.request.Request(LAMA_URL, headers={"User-Agent": "editor-360"})
        sha = hashlib.sha256()
        self.job.update(total=LAMA_BYTES, done=0)
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha.update(chunk)
                    self.job["done"] += len(chunk)
            if self.job["done"] != LAMA_BYTES or sha.hexdigest() != LAMA_SHA256:
                raise RuntimeError("La descarga del modelo no coincide con el original "
                                   "(archivo incompleto o distinto). Vuelve a intentarlo.")
        except Exception:
            part.unlink(missing_ok=True)
            raise
        part.replace(LAMA_FILE)

    def remove_model(self):
        with self.load_lock:
            self.session = None
        LAMA_FILE.unlink(missing_ok=True)

    # --- uso -----------------------------------------------------------
    def check_key(self, key, model):
        return None

    # Preparar el modelo en memoria puede tardar desde unos segundos hasta más de un
    # minuto en equipos con poca RAM libre. Se hace una sola vez por arranque y se
    # adelanta en segundo plano (warmup) en cuanto el usuario elige la IA local.
    def loaded(self):
        return self.session is not None

    def warmup(self):
        if self.session is None and self.status()["ready"] and not self.loading:
            self.loading = True
            threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        try:
            self._session()
        except Exception:
            pass
        finally:
            self.loading = False

    def _session(self):
        with self.load_lock:
            if self.session is None:
                if not self.status()["ready"]:
                    raise ProviderError("El modo local no está instalado. Instálalo en Ajustes.")
                import onnxruntime as ort
                self.session = ort.InferenceSession(str(LAMA_FILE), providers=["CPUExecutionProvider"])
            return self.session

    def edit(self, key, model, photo, mask, mode, text):
        if mode != "remove":
            raise ProviderError("El modo local solo puede quitar elementos.")
        sess = self._session()
        n = photo.width
        m = np.asarray(mask) > 127
        ys, xs = np.nonzero(m)
        # Se trabaja sobre un recorte cuadrado alrededor de la máscara (más detalle que reducir toda la vista)
        cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
        side = int(min(n, max(512, (max(xs.max() - xs.min(), ys.max() - ys.min()) + 1) * 2)))
        x0 = int(np.clip(cx - side / 2, 0, n - side))
        y0 = int(np.clip(cy - side / 2, 0, n - side))
        box = (x0, y0, x0 + side, y0 + side)
        crop = photo.crop(box).resize((512, 512), Image.LANCZOS)
        mcrop = mask.crop(box).resize((512, 512), Image.NEAREST)

        img_in = np.asarray(crop, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        mask_in = (np.asarray(mcrop, dtype=np.float32)[None, None] > 127).astype(np.float32)
        inputs = sess.get_inputs()
        feed = {}
        for inp in inputs:
            feed[inp.name] = mask_in if (inp.shape and len(inp.shape) == 4 and inp.shape[1] == 1) \
                or "mask" in inp.name.lower() else img_in
        out = sess.run(None, feed)[0][0]
        if out.shape[0] == 3:
            out = out.transpose(1, 2, 0)
        if out.max() <= 2.0:            # algunos modelos devuelven 0..1, otros 0..255
            out = out * 255.0
        filled = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).resize((side, side), Image.LANCZOS)
        result = photo.copy()
        result.paste(filled, box[:2])
        return result


PROVIDERS = {p.id: p for p in (Gemini(), OpenAI(), Stability(), FluxReplicate(), LocalLama())}
LOCAL = PROVIDERS["local"]
