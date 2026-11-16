# Editor 360 · IA

Editor local de fotografías 360° (equirectangulares) que usa inteligencia artificial para **quitar elementos** de una panorámica o **añadir elementos nuevos**. Puedes elegir la IA: Google Gemini, OpenAI, Stability AI, FLUX (vía Replicate) o un modo local gratuito.

## Cómo funciona

1. Abres desde tu ordenador la foto 360 que quieres editar y la recorres en un visor 360 ([Pannellum](https://pannellum.org/)).
2. Encuadras la zona a editar. El editor convierte esa vista en una foto plana, sin la distorsión de la proyección equirectangular, incluso en el techo, el suelo o la costura de la imagen.
3. Pintas una máscara sobre lo que quieres quitar, o sobre donde quieres añadir algo, y lo describes con texto.
4. La IA elegida edita la vista. Del resultado solo se usa lo que hay dentro de la máscara, con bordes suavizados y el color igualado al resto de la foto.
5. La vista editada se vuelve a proyectar sobre la panorámica a su **resolución original**. Puedes encadenar varias ediciones y deshacerlas.
6. Guardas sobrescribiendo la foto o con otro nombre. Se conservan los metadatos de la foto original (EXIF, perfil de color y datos 360/GPano), para que otros visores la sigan reconociendo como 360.

## Proveedores de IA

| IA | Quitar | Añadir | Clave | Notas |
| --- | :---: | :---: | --- | --- |
| **Google Gemini** | ✓ | ✓ | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Entiende instrucciones en español. |
| **OpenAI** (`gpt-image-2`) | ✓ | ✓ | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Muy bueno añadiendo objetos. |
| **Stability AI** | ✓ | ✓ | [platform.stability.ai](https://platform.stability.ai/account/keys) | Quitar usa *Erase*; añadir usa *Inpaint*. Para añadir, escribe en inglés. |
| **FLUX Fill** (Replicate) | ✓ | ✓ | [replicate.com](https://replicate.com/account/api-tokens) | Modelo abierto FLUX. Para añadir, escribe en inglés. |
| **Local** (LaMa) | ✓ | — | No necesita | Gratis y sin internet tras una descarga única de ~208 MB. Solo quita. |

Los servicios de pago cobran cada edición a **tu cuenta**: necesitan facturación o saldo activos.

Puedes configurar varias IA a la vez y elegir cuál usar en cada edición desde el panel de edición.

## Requisitos

- Python 3.10 o superior
- **Google Chrome o Microsoft Edge** para poder sobrescribir archivos. En otros navegadores el editor funciona, pero guardar descarga una copia.

```bash
pip install -r requirements.txt
```

## Puesta en marcha

1. Arranca el editor:

   ```bash
   python server.py
   ```

   En Windows también puedes hacer doble clic en `Abrir editor.bat`. Para usar otro puerto: `python server.py 8400`.

2. Abre <http://localhost:8361>.
3. La primera vez, el editor te pide que elijas una IA y pongas **tu propia API key**, o que instales el modo local. Cada clave se comprueba con su proveedor antes de guardarla, sin generar nada ni gastar saldo.

## Tus API keys

- Cada persona usa **sus propias claves**. El repositorio no incluye ninguna.
- Se guardan solo en tu ordenador, en `config.json`, que está en `.gitignore`: nunca se suben a GitHub y cada una solo se envía a su proveedor.
- Puedes añadir, cambiar o quitar claves y cambiar de IA cuando quieras desde **Ajustes**, sin reiniciar el editor.
- Si prefieres no guardarlas en un archivo, usa variables de entorno. Tienen prioridad sobre `config.json` y nunca se escriben en él:

  | IA | Variable |
  | --- | --- |
  | Gemini | `GEMINI_API_KEY` |
  | OpenAI | `OPENAI_API_KEY` |
  | Stability AI | `STABILITY_API_KEY` |
  | Replicate | `REPLICATE_API_TOKEN` |

## Modo local

El modo local borra objetos con [LaMa](https://github.com/advimman/lama) en tu propio ordenador, sin clave y sin enviar nada a internet.

- No se descarga nada hasta que lo eliges en **Ajustes**. Antes de descargar, el editor te dice cuánto ocupará.
- Descarga el modelo `lama_fp32.onnx` (~208 MB, licencia Apache-2.0) de [Carve/LaMa-ONNX](https://huggingface.co/Carve/LaMa-ONNX) a la carpeta `modelos/`, y comprueba su huella SHA-256 antes de usarlo. Si falta el paquete de Python `onnxruntime` (~15 MB), lo instala.
- Puedes borrar el modelo desde **Ajustes** para liberar espacio.
- Solo sirve para **quitar** elementos.

## Uso

| Paso | Qué hacer |
| --- | --- |
| Abrir | Pulsa **Abrir foto…** (`Ctrl+O`) y busca la foto en tu ordenador, o arrástrala sobre el visor. |
| Encuadrar | Gira y haz zoom hasta que el objeto quede **entero** dentro del recuadro blanco. Cuanto más cerca, más detalle. |
| Editar | Pulsa **Editar esta vista**, elige la IA, *Quitar* o *Añadir*, pinta la máscara (teclas `[` y `]` para el tamaño del pincel, `e` para la goma) y escribe qué quitar o añadir. |
| Revisar | Mantén pulsado *Ver el original* para comparar. Pide *Otra variante*, o prueba con otra IA, si no te convence. |
| Aplicar | **Aplicar a la panorámica** la fusiona a resolución completa. **Deshacer** revierte las últimas 15 ediciones. |
| Guardar | **Guardar…** (`Ctrl+S`) ofrece dos opciones: **Sobrescribir** el archivo que estás editando, o **Guardar como…** con otro nombre y carpeta. Después de *Guardar como*, sigues trabajando sobre el archivo nuevo. |

Si la máscara toca el borde del recuadro, el editor avisa: lo que quede fuera del recuadro no se editará.

Las fotos no se suben a internet salvo el recorte que se envía a la IA elegida (ninguno en modo local): el editor funciona en tu ordenador (`localhost`) y es tu navegador quien lee y escribe los archivos.

## Estructura

```
server.py      Servidor local, reproyección 360 y fusión del resultado
providers.py   Proveedores de IA (Gemini, OpenAI, Stability, Replicate, LaMa local)
index.html     Interfaz del editor
config.json    Tus claves y preferencias (se crea solo; no se sube a git)
modelos/       Modelo local, si lo descargas (no se sube a git)
```
