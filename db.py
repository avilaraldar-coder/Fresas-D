"""Capa de datos del sistema.

Usa las mismas consultas con dos motores:
  * SQLite (archivo local)  -> para probar en tu computadora.
  * PostgreSQL (Neon, etc.) -> para publicar: los datos no se pierden al reiniciar.

Las consultas usan parámetros con nombre (:nombre). Se abre una conexión corta por
operación, así no hay conexiones colgadas cuando la base gratuita se "duerme".
"""
import random
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pandas as pd

ARCHIVO_LOCAL = Path(__file__).resolve().with_name("fresas.db")
TTL = 120  # segundos que se recuerda una lectura (se limpia al guardar cualquier cambio)

_url = None
_cache = {}
_lock = threading.Lock()


def configure(url=None):
    """Sin url usa SQLite local; con url (postgresql://...) usa PostgreSQL."""
    global _url
    _url = url or None


def es_pg():
    return bool(_url)


# ---------------------------------------------------------------- conexión
def _conectar():
    if _url:
        import psycopg2
        return psycopg2.connect(_url.replace("postgres://", "postgresql://", 1), connect_timeout=15)
    return sqlite3.connect(ARCHIVO_LOCAL, timeout=15)


def _sql(sql):
    """:nombre -> %(nombre)s cuando el motor es PostgreSQL."""
    return re.sub(r"(?<![:\w]):([A-Za-z_]\w*)", r"%(\1)s", sql) if _url else sql


def limpiar(v):
    """Convierte valores de numpy/pandas a tipos simples que aceptan ambas bases."""
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, bool):
        return int(v)
    if v is None or isinstance(v, (str, bytes, int)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _ejecutar(cur, sql, p):
    datos = {k: limpiar(v) for k, v in p.items()}
    if datos:
        cur.execute(_sql(sql), datos)
    else:
        cur.execute(sql)
    return cur


# ---------------------------------------------------------------- lectura / escritura
def query(sql, **p):
    """Devuelve un DataFrame. Las lecturas repetidas se sirven de memoria unos segundos."""
    clave = (bool(_url), sql, tuple(sorted((k, limpiar(v)) for k, v in p.items())))
    with _lock:
        hit = _cache.get(clave)
    if hit and time.time() - hit[0] < TTL:
        return hit[1].copy()
    con = _conectar()
    try:
        cur = _ejecutar(con.cursor(), sql, p)
        filas, cols = cur.fetchall(), [d[0] for d in cur.description]
    finally:
        con.close()
    df = pd.DataFrame(filas, columns=cols).infer_objects()
    with _lock:
        _cache[clave] = (time.time(), df)
    return df.copy()


@contextmanager
def tx():
    """Transacción: todo se guarda junto o no se guarda nada.

    with db.tx() as ex:
        ex("UPDATE ...", a=1)
        filas = ex("SELECT ...").fetchall()
    """
    con = _conectar()
    try:
        def ex(sql, **p):
            return _ejecutar(con.cursor(), sql, p)
        yield ex
        con.commit()
        with _lock:
            _cache.clear()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def ejecutar(sql, **p):
    with tx() as ex:
        ex(sql, **p)


def _insertar(ex, tabla, cols, filas, bloque=100):
    for i in range(0, len(filas), bloque):
        parte = filas[i:i + bloque]
        marcas = ",".join("(" + ",".join(f":{c}_{j}" for c in cols) + ")" for j in range(len(parte)))
        params = {f"{c}_{j}": v for j, fila in enumerate(parte) for c, v in zip(cols, fila)}
        ex(f"INSERT INTO {tabla}({','.join(cols)}) VALUES {marcas}", **params)


def insert_many(tabla, cols, filas):
    with tx() as ex:
        _insertar(ex, tabla, cols, filas)


# ---------------------------------------------------------------- esquema
def _tablas():
    pk = "SERIAL PRIMARY KEY" if _url else "INTEGER PRIMARY KEY AUTOINCREMENT"
    n = "DOUBLE PRECISION DEFAULT 0"
    return [
        f"CREATE TABLE IF NOT EXISTS productos(id {pk}, nombre TEXT, categoria TEXT, tamano TEXT, precio {n}, "
        f"costo_insumos {n}, costo_empaque {n}, orden INTEGER DEFAULT 0, activo INTEGER DEFAULT 1)",
        f"CREATE TABLE IF NOT EXISTS insumos(id {pk}, nombre TEXT, unidad TEXT, stock {n}, minimo {n}, costo {n})",
        f"CREATE TABLE IF NOT EXISTS recetas(id {pk}, producto_id INTEGER, insumo_id INTEGER, cantidad {n})",
        f"CREATE TABLE IF NOT EXISTS ventas(id {pk}, fecha TEXT, producto_id INTEGER, cantidad INTEGER, pedido TEXT)",
        f"CREATE TABLE IF NOT EXISTS gastos(id {pk}, concepto TEXT, tipo TEXT, monto {n})",
        "CREATE TABLE IF NOT EXISTS config(clave TEXT PRIMARY KEY, valor DOUBLE PRECISION)",
        "CREATE TABLE IF NOT EXISTS ajustes(clave TEXT PRIMARY KEY, valor TEXT)",
        "CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas(fecha)",
    ]


# ---------------------------------------------------------------- datos iniciales (catálogo de ejemplo)
AJUSTES = {"nombre": "Zona Fresa", "eslogan": "Exitos Dani",
           "emoji": "🍓", "moneda": "Q", "categorias": "Fresas,Bebidas,"}
CONFIG = {"merma": 8, "comision": 3.5, "impuestos": 5, "meta": 50}

# nombre, categoría, tamaño, precio, costo insumos, costo empaque, peso de ventas (solo para el demo)
PRODUCTOS = [
    ("Fresas con crema", "Fresas", "Pequeño 8oz", 15, 5.5, 1.2, .15),
    ("Fresas con crema", "Fresas", "Mediano 12oz", 25, 9, 1.5, .20),
    ("Fresas con crema", "Fresas", "Grande 16oz", 35, 13.5, 1.8, .10),
    ("Fresas con chocolate", "Fresas", "Mediano 12oz", 30, 11.5, 1.5, .12),
    ("Fresas bañadas", "Fresas", "Caja x6", 45, 17, 3, .05),
    ("Matcha latte frío", "Bebidas", "12oz", 28, 9, 1.5, .14),
    ("Matcha latte frío", "Bebidas", "16oz", 35, 11.5, 1.8, .10),
    ("Matcha fresa", "Bebidas", "16oz", 38, 13, 1.8, .09),
    ("Matcha caliente", "Bebidas", "12oz", 25, 7.5, 1.2, .05),
]
INSUMOS = [("Fresa", "kg", 12, 5, 28), ("Crema", "litros", 8, 3, 30), ("Chocolate", "kg", 3, 1, 70),
           ("Matcha en polvo", "g", 400, 150, .9), ("Leche", "litros", 20, 8, 12), ("Jarabe", "litros", 3, 1, 25),
           ("Vasos y cajas", "unidades", 200, 60, .8)]
RECETAS = {
    "Fresas con crema Pequeño 8oz": {"Fresa": .12, "Crema": .06},
    "Fresas con crema Mediano 12oz": {"Fresa": .18, "Crema": .09},
    "Fresas con crema Grande 16oz": {"Fresa": .25, "Crema": .12},
    "Fresas con chocolate Mediano 12oz": {"Fresa": .18, "Chocolate": .05},
    "Fresas bañadas Caja x6": {"Fresa": .3, "Chocolate": .1},
    "Matcha latte frío 12oz": {"Matcha en polvo": 4, "Leche": .25, "Jarabe": .02},
    "Matcha latte frío 16oz": {"Matcha en polvo": 5, "Leche": .35, "Jarabe": .03},
    "Matcha fresa 16oz": {"Matcha en polvo": 5, "Fresa": .08, "Leche": .3},
    "Matcha caliente 12oz": {"Matcha en polvo": 4, "Leche": .25},
}
GASTOS = [("Alquiler del local", "Operativo", 3500), ("Salarios (2 empleados)", "Personal", 5200),
          ("Retribución del dueño", "Dueño", 4000), ("Luz", "Operativo", 650), ("Agua", "Operativo", 150),
          ("Internet y teléfono", "Operativo", 300), ("Redes sociales y publicidad", "Marketing", 400),
          ("Mantenimiento de equipo", "Operativo", 300), ("Depreciación (refri, licuadoras)", "Operativo", 250),
          ("Contador", "Operativo", 400)]
PESO_DEMO = {f"{n} {t}": w for n, _, t, *_, w in PRODUCTOS}


def init():
    """Crea las tablas y, la primera vez, el catálogo de ejemplo (sin ventas)."""
    with tx() as ex:
        for s in _tablas():
            ex(s)
        for k, v in AJUSTES.items():
            ex("INSERT INTO ajustes(clave, valor) VALUES(:k, :v) ON CONFLICT(clave) DO NOTHING", k=k, v=v)
        for k, v in CONFIG.items():
            ex("INSERT INTO config(clave, valor) VALUES(:k, :v) ON CONFLICT(clave) DO NOTHING", k=k, v=v)
        if ex("SELECT COUNT(*) FROM ajustes WHERE clave = 'sembrado'").fetchone()[0]:
            return
        _insertar(ex, "productos", ["nombre", "categoria", "tamano", "precio", "costo_insumos", "costo_empaque", "orden"],
                  [(n, c, t, pr, ci, ce, i) for i, (n, c, t, pr, ci, ce, _) in enumerate(PRODUCTOS, 1)])
        _insertar(ex, "insumos", ["nombre", "unidad", "stock", "minimo", "costo"], INSUMOS)
        _insertar(ex, "gastos", ["concepto", "tipo", "monto"], GASTOS)
        prod = {f"{n} {t}": i for i, n, t in ex("SELECT id, nombre, tamano FROM productos").fetchall()}
        ins = {n: i for i, n in ex("SELECT id, nombre FROM insumos").fetchall()}
        _insertar(ex, "recetas", ["producto_id", "insumo_id", "cantidad"],
                  [(prod[p], ins[i], q) for p, d in RECETAS.items() for i, q in {**d, "Vasos y cajas": 1}.items()])
        ex("INSERT INTO ajustes(clave, valor) VALUES('sembrado', '1')")


# ---------------------------------------------------------------- ventas de ejemplo (para mostrar el sistema)
def cargar_demo(hoy, dias=60):
    prods = query("SELECT id, nombre, tamano FROM productos WHERE activo = 1")
    rnd, filas = random.Random(7), []
    for d in range(dias - 1, -1, -1):
        f = hoy - timedelta(days=d)
        k = 1.4 if f.weekday() >= 4 else 1
        for r in prods.itertuples():
            mu = 46 * PESO_DEMO.get(f"{r.nombre} {r.tamano}", .05) * k
            q = max(0, int(rnd.gauss(mu, mu * .3)))
            if q:
                filas.append((f.isoformat(), r.id, q, "demo"))
    if filas:
        insert_many("ventas", ["fecha", "producto_id", "cantidad", "pedido"], filas)
    return len(filas)


def borrar_demo():
    ejecutar("DELETE FROM ventas WHERE pedido = 'demo'")
