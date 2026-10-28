# Sistema de gestión · Zona Fresa

App web (Streamlit) para ventas, inventario, costos y metas. Se abre desde el navegador del celular,
la tablet o la computadora; no hay que instalar nada.

## Archivos
| Archivo | Para qué sirve |
|---|---|
| `app.py` | Pantallas |
| `modelo.py` | Cálculos de costos, márgenes, ventas e inventario |
| `db.py` | Base de datos (SQLite local o PostgreSQL en la nube) |
| `.streamlit/config.toml` | **Diseño**: colores, tipografía, esquinas |
| `logo.png` (opcional) | Si lo agregas junto a `app.py`, aparece arriba del menú |

## Probar en tu computadora
```bash
pip install -r requirements.txt
streamlit run app.py
```
Sin configuración usa una base local (`fresas.db`) y no pide contraseña. Sirve solo para probar.
Para enseñarle el sistema al cliente: **Ajustes → Cargar ventas de ejemplo**; después, **Borrar ventas de ejemplo**.

## Publicar (gratis)
1. **Base de datos permanente:** crea una cuenta en https://neon.com, un proyecto nuevo y copia la
   cadena de conexión (`postgresql://...`). Usa la versión "pooled" y que termine en `?sslmode=require`.
2. **Código:** sube esta carpeta a un repositorio de GitHub. El `.gitignore` ya evita subir secretos.
3. **Hosting:** en https://share.streamlit.io elige "New app", selecciona el repositorio y `app.py`.
4. **Secretos:** en *Settings → Secrets* pega el contenido de `.streamlit/secrets.toml.example`
   con tus valores (`DATABASE_URL`, `APP_PASSWORD`, y opcionalmente `CAJERO_PASSWORD`).
5. Comparte el link con el cliente. En el celular: menú del navegador → **Agregar a pantalla de inicio**.

La primera vez la app crea las tablas y un catálogo de ejemplo (productos, insumos, recetas, gastos),
sin ventas. Se ajusta todo desde la misma app.

## Cosas que conviene saber
- **Contraseñas:** `APP_PASSWORD` entra a todo. `CAJERO_PASSWORD` (opcional) solo ve Vender, sin costos ni utilidades.
  Si recargas la página, vuelve a pedir la contraseña.
- **Streamlit Community Cloud** es gratis, pero la app se duerme tras 12 horas sin visitas y alguien debe tocar
  "wake up" (los datos no se pierden, están en Neon). Para algo sin esa espera, usa un hosting de pago bajo (Render, Railway).
- **Neon gratis** está pensado para proyectos pequeños; para este volumen de datos alcanza. Si el cliente
  depende de la app todos los días, considera su plan de pago (se cobra por uso).
- **Respaldo:** *Ajustes → Descargar respaldo en Excel*. Recomienda al cliente hacerlo cada mes.
- **Fecha de las ventas:** usa la zona horaria `TIMEZONE` (por defecto Guatemala), no la del servidor.
