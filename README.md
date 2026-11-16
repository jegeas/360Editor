# Editor 360 · IA

Editor local de fotografías 360° (equirectangulares) que usa la IA de Google Gemini para **quitar elementos** de una panorámica o **añadir elementos nuevos**.

## Cómo funciona

1. Abres una panorámica y la recorres en un visor 360 ([Pannellum](https://pannellum.org/)).
2. Encuadras la zona a editar. El editor convierte esa vista en una foto plana, sin la distorsión de la proyección equirectangular, incluso en el techo, el suelo o la costura de la imagen.
3. Pintas una máscara sobre lo que quieres quitar, o sobre donde quieres añadir algo, y lo describes con texto.
4. Gemini edita la vista. Del resultado solo se usa lo que hay dentro de la máscara, con bordes suavizados y el color igualado al resto de la foto.
5. La vista editada se vuelve a proyectar sobre la panorámica a su **resolución original**. Puedes encadenar varias ediciones, deshacerlas y guardar una copia.

El original nunca se modifica: las copias se guardan en `editadas/nombre-editada.jpg` dentro de la carpeta de panorámicas.

## Requisitos

- Python 3.10 o superior
- Una API key de [Google AI Studio](https://aistudio.google.com/apikey) con facturación o saldo activos (los modelos de imagen no entran en el uso gratuito)

```bash
pip install -r requirements.txt
```

## Puesta en marcha

1. Copia `config.example.json` como `config.json` y pon tu API key. También puedes ponerla después en **Ajustes** dentro del editor, o usar la variable de entorno `GEMINI_API_KEY`.
2. Copia tus panorámicas en la carpeta `fotos/`, o indica otra carpeta en **Ajustes**.
3. Arranca el editor:

   ```bash
   python server.py
   ```

   En Windows también puedes hacer doble clic en `Abrir editor.bat`.

4. Abre <http://localhost:8361>.

## Uso

| Paso | Qué hacer |
| --- | --- |
| Elegir foto | Selecciónala en el desplegable superior. |
| Encuadrar | Gira y haz zoom hasta que el objeto quede **entero** dentro del recuadro blanco. Cuanto más cerca, más detalle. |
| Editar | Pulsa **Editar esta vista**, elige *Quitar* o *Añadir*, pinta la máscara (teclas `[` y `]` para el tamaño del pincel, `e` para la goma) y escribe qué quitar o añadir. |
| Revisar | Mantén pulsado *Ver el original* para comparar. Pide *Otra variante* si no te convence. |
| Aplicar | **Aplicar a la panorámica** la fusiona a resolución completa. **Deshacer** revierte las últimas 15 ediciones. |
| Guardar | **Guardar copia** escribe `editadas/<nombre>-editada.jpg`. |

Si la máscara toca el borde del recuadro, el editor avisa: lo que quede fuera del recuadro no se editará.

## Configuración (`config.json`)

| Clave | Por defecto | Descripción |
| --- | --- | --- |
| `api_key` | — | API key de Gemini. `GEMINI_API_KEY` tiene prioridad. |
| `model` | `gemini-3.1-flash-image` | Modelo de imagen de Gemini. |
| `photos_dir` | `fotos` | Carpeta de panorámicas: ruta absoluta o relativa al editor. |

El puerto se cambia con la variable de entorno `EDITOR_PORT` (por defecto `8361`).

`config.json` y el contenido de `fotos/` están en `.gitignore`: tu clave y tus fotos no se suben al repositorio.

## Estructura

```
server.py            Servidor local, reproyección y llamada a Gemini
index.html           Interfaz del editor
config.example.json  Plantilla de configuración
fotos/               Panorámicas (no se suben a git)
```
