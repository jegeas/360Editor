# Editor 360 · IA

Editor local de fotografías 360° (equirectangulares) que usa la IA de Google Gemini para **quitar elementos** de una panorámica o **añadir elementos nuevos**.

## Cómo funciona

1. Abres desde tu ordenador la foto 360 que quieres editar y la recorres en un visor 360 ([Pannellum](https://pannellum.org/)).
2. Encuadras la zona a editar. El editor convierte esa vista en una foto plana, sin la distorsión de la proyección equirectangular, incluso en el techo, el suelo o la costura de la imagen.
3. Pintas una máscara sobre lo que quieres quitar, o sobre donde quieres añadir algo, y lo describes con texto.
4. Gemini edita la vista. Del resultado solo se usa lo que hay dentro de la máscara, con bordes suavizados y el color igualado al resto de la foto.
5. La vista editada se vuelve a proyectar sobre la panorámica a su **resolución original**. Puedes encadenar varias ediciones y deshacerlas.
6. Guardas sobrescribiendo la foto o con otro nombre. Se conservan los metadatos de la foto original (EXIF, perfil de color y datos 360/GPano), para que otros visores la sigan reconociendo como 360.

## Requisitos

- Python 3.10 o superior
- **Google Chrome o Microsoft Edge** para poder sobrescribir archivos. En otros navegadores el editor funciona, pero guardar descarga una copia.
- Una API key de [Google AI Studio](https://aistudio.google.com/apikey) con facturación o saldo activos (los modelos de imagen no entran en el uso gratuito)

```bash
pip install -r requirements.txt
```

## Puesta en marcha

1. Arranca el editor:

   ```bash
   python server.py
   ```

   En Windows también puedes hacer doble clic en `Abrir editor.bat`.

2. Abre <http://localhost:8361>.
3. La primera vez, el editor te pide **tu propia API key** de Gemini. Créala en [aistudio.google.com/apikey](https://aistudio.google.com/apikey) y pégala. El editor la comprueba con Google antes de guardarla.

## Tu API key

- Cada persona usa **su propia clave**. El repositorio no incluye ninguna.
- Se guarda solo en tu ordenador, en `config.json`, que está en `.gitignore`: nunca se sube a GitHub y solo se envía a Google.
- Puedes cambiarla o quitarla cuando quieras desde **Ajustes**, sin reiniciar el editor.
- Si prefieres no guardarla en un archivo, define la variable de entorno `GEMINI_API_KEY`. Tiene prioridad sobre `config.json` y nunca se escribe en él.

## Uso

| Paso | Qué hacer |
| --- | --- |
| Abrir | Pulsa **Abrir foto…** (`Ctrl+O`) y busca la foto en tu ordenador, o arrástrala sobre el visor. |
| Encuadrar | Gira y haz zoom hasta que el objeto quede **entero** dentro del recuadro blanco. Cuanto más cerca, más detalle. |
| Editar | Pulsa **Editar esta vista**, elige *Quitar* o *Añadir*, pinta la máscara (teclas `[` y `]` para el tamaño del pincel, `e` para la goma) y escribe qué quitar o añadir. |
| Revisar | Mantén pulsado *Ver el original* para comparar. Pide *Otra variante* si no te convence. |
| Aplicar | **Aplicar a la panorámica** la fusiona a resolución completa. **Deshacer** revierte las últimas 15 ediciones. |
| Guardar | **Guardar…** (`Ctrl+S`) ofrece dos opciones: **Sobrescribir** el archivo que estás editando, o **Guardar como…** con otro nombre y carpeta. Después de *Guardar como*, sigues trabajando sobre el archivo nuevo. |

Si la máscara toca el borde del recuadro, el editor avisa: lo que quede fuera del recuadro no se editará.

Las fotos no se suben a internet salvo el recorte que se envía a Gemini: el editor funciona en tu ordenador (`localhost`) y es tu navegador quien lee y escribe los archivos.

## Configuración (`config.json`)

El editor crea este archivo al guardar tus Ajustes; no hace falta tocarlo a mano.

| Clave | Por defecto | Descripción |
| --- | --- | --- |
| `api_key` | — | Tu API key de Gemini. `GEMINI_API_KEY` tiene prioridad. |
| `model` | `gemini-3.1-flash-image` | Modelo de imagen de Gemini. |

El puerto se cambia con la variable de entorno `EDITOR_PORT` (por defecto `8361`).

## Estructura

```
server.py            Servidor local, reproyección y llamada a Gemini
index.html           Interfaz del editor
config.example.json  Plantilla de configuración
```
