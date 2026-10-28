"""Reglas del negocio (sin nada de interfaz): costos, márgenes, ventas e inventario."""
import io
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import db

TZ = "America/Guatemala"  # el servidor suele estar en UTC; las ventas deben llevar la fecha local
NUMERICAS = {"precio", "costo_insumos", "costo_empaque", "stock", "minimo", "costo", "monto", "cantidad", "orden"}


def ahora():
    return datetime.now(ZoneInfo(TZ))


def hoy():
    return ahora().date()


def _num(df, *cols):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


# ---------------------------------------------------------------- configuración
def cfg():
    d = db.query("SELECT clave, valor FROM config")
    return dict(zip(d.clave, d.valor.astype(float)))


def ajustes():
    d = db.query("SELECT clave, valor FROM ajustes")
    return dict(zip(d.clave, d.valor))


def guardar_ajustes(valores):
    with db.tx() as ex:
        for k, v in valores.items():
            ex("INSERT INTO ajustes(clave, valor) VALUES(:k, :v) "
               "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", k=k, v=str(v))


def guardar_reglas(valores):
    with db.tx() as ex:
        for k, v in valores.items():
            ex("UPDATE config SET valor = :v WHERE clave = :k", k=k, v=float(v))


# ---------------------------------------------------------------- lecturas y cálculos
def productos():
    df, c = db.query("SELECT * FROM productos ORDER BY orden, id"), cfg()
    pct = (c["comision"] + c["impuestos"]) / 100
    _num(df, "precio", "costo_insumos", "costo_empaque")
    df["activo"] = pd.to_numeric(df["activo"], errors="coerce").fillna(1).astype(bool)
    df["etq"] = (df["nombre"].fillna("").astype(str) + " " + df["tamano"].fillna("").astype(str)).str.strip()
    base = df.costo_insumos * (1 + c["merma"] / 100) + df.costo_empaque
    df["costo_real"] = base + df.precio * pct
    df["margen_q"] = df.precio - df.costo_real
    df["margen_pct"] = df.margen_q / df.precio.where(df.precio > 0) * 100
    df["precio_sugerido"] = base / max(1 - pct - c["meta"] / 100, 0.05)
    return df


COLUMNAS_VENTAS = ["fecha", "producto_id", "cantidad", "etq", "categoria", "ingreso", "costo", "utilidad"]


def ventas(dias):
    desde = (hoy() - timedelta(days=dias - 1)).isoformat()
    v = db.query("SELECT fecha, producto_id, cantidad FROM ventas WHERE fecha >= :d", d=desde)
    if v.empty:
        return pd.DataFrame({c: [] for c in COLUMNAS_VENTAS})
    v = v.merge(productos(), left_on="producto_id", right_on="id")
    v["ingreso"], v["costo"] = v.cantidad * v.precio, v.cantidad * v.costo_real
    v["utilidad"] = v.ingreso - v.costo
    return v


def ultimos_registros(n=15):
    return db.query("SELECT v.fecha, p.nombre || ' ' || p.tamano AS producto, v.cantidad "
                    "FROM ventas v JOIN productos p ON p.id = v.producto_id ORDER BY v.id DESC LIMIT " + str(int(n)))


def gastos():
    return _num(db.query("SELECT * FROM gastos ORDER BY id"), "monto")


def insumos():
    return _num(db.query("SELECT * FROM insumos ORDER BY nombre"), "stock", "minimo", "costo")


def bajos():
    i = insumos()
    return i[i.stock <= i.minimo]


# ---------------------------------------------------------------- ventas e inventario
def _mover_stock(ex, producto_id, cantidad, signo):
    """Descuenta (signo -1) o devuelve (+1) los insumos que lleva la receta del producto."""
    for insumo_id, por_unidad in ex("SELECT insumo_id, cantidad FROM recetas WHERE producto_id = :p",
                                    p=producto_id).fetchall():
        ex("UPDATE insumos SET stock = stock + :d WHERE id = :i", d=signo * (por_unidad or 0) * cantidad, i=insumo_id)


def cobrar(carrito, fecha):
    """Registra todo el pedido junto (mismo código de pedido) y baja el inventario."""
    pedido = uuid.uuid4().hex[:10]
    with db.tx() as ex:
        for pid, q in carrito.items():
            ex("INSERT INTO ventas(fecha, producto_id, cantidad, pedido) VALUES(:f, :p, :q, :o)",
               f=fecha.isoformat(), p=int(pid), q=int(q), o=pedido)
            _mover_stock(ex, int(pid), int(q), -1)


def deshacer():
    """Deshace el último pedido completo. Devuelve un texto para mostrar."""
    with db.tx() as ex:
        ultima = ex("SELECT id, pedido FROM ventas ORDER BY id DESC LIMIT 1").fetchone()
        if not ultima:
            return "No hay ventas para deshacer"
        vid, pedido = ultima
        if pedido and pedido != "demo":
            lineas = ex("SELECT id, producto_id, cantidad FROM ventas WHERE pedido = :o", o=pedido).fetchall()
        else:
            lineas = ex("SELECT id, producto_id, cantidad FROM ventas WHERE id = :i", i=vid).fetchall()
        for lid, pid, q in lineas:
            if pedido != "demo":  # las ventas de ejemplo nunca movieron inventario
                _mover_stock(ex, pid, q, +1)
            ex("DELETE FROM ventas WHERE id = :i", i=lid)
    return f"Se deshizo el último pedido ({len(lineas)} producto{'s' if len(lineas) != 1 else ''})"


def mover_insumo(insumo_id, delta):
    db.ejecutar("UPDATE insumos SET stock = stock + :d WHERE id = :i", d=delta, i=int(insumo_id))


# ---------------------------------------------------------------- guardar tablas editables
def _valor(col, v):
    v = db.limpiar(v)
    if v is None:
        return 0 if col in NUMERICAS else (1 if col == "activo" else None)
    return v


def guardar_tabla(tabla, cols, original, editado, requeridos):
    """Sincroniza lo que se editó en pantalla (altas, cambios y bajas). Devuelve avisos."""
    requeridos = [requeridos] if isinstance(requeridos, str) else requeridos
    ed = editado.dropna(subset=requeridos)
    for r in requeridos:
        ed = ed[ed[r].astype(str).str.strip() != ""]
    mantener = {int(i) for i in ed["id"].dropna()}
    borrar = {int(i) for i in original["id"].dropna()} - mantener
    sig = pd.to_numeric(ed["orden"], errors="coerce").max() if "orden" in ed else 0
    sig = 0 if pd.isna(sig) else int(sig)
    avisos, desactivados = [], 0
    with db.tx() as ex:
        for i in borrar:
            if tabla == "productos":
                if ex("SELECT COUNT(*) FROM ventas WHERE producto_id = :i", i=i).fetchone()[0]:
                    ex("UPDATE productos SET activo = 0 WHERE id = :i", i=i)  # con historial: se oculta, no se borra
                    desactivados += 1
                    continue
                ex("DELETE FROM recetas WHERE producto_id = :i", i=i)
            elif tabla == "insumos":
                ex("DELETE FROM recetas WHERE insumo_id = :i", i=i)
            ex(f"DELETE FROM {tabla} WHERE id = :i", i=i)
        for _, r in ed.iterrows():
            vals = {c: _valor(c, r[c]) for c in cols}
            if pd.isna(r["id"]):
                if "orden" in cols and not db.limpiar(r["orden"]):
                    sig += 1
                    vals["orden"] = sig
                ex(f"INSERT INTO {tabla}({','.join(cols)}) VALUES({','.join(':' + c for c in cols)})", **vals)
            else:
                ex(f"UPDATE {tabla} SET {','.join(c + '=:' + c for c in cols)} WHERE id = :_id", _id=int(r["id"]), **vals)
    if desactivados:
        avisos.append(f"{desactivados} producto(s) con ventas registradas se ocultaron en lugar de borrarse, "
                      "para no perder el historial.")
    return avisos


# ---------------------------------------------------------------- respaldo
def excel():
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as w:
        productos().drop(columns=["id"]).to_excel(w, sheet_name="Productos", index=False)
        ventas(3650)[["fecha", "etq", "cantidad", "ingreso", "costo", "utilidad"]].to_excel(w, sheet_name="Ventas", index=False)
        gastos().drop(columns=["id"]).to_excel(w, sheet_name="Gastos fijos", index=False)
        insumos().drop(columns=["id"]).to_excel(w, sheet_name="Inventario", index=False)
    return b.getvalue()
