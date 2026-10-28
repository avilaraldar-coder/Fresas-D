"""Sistema de gestión para negocio de fresas y bebidas.

Colores y tipografía: .streamlit/config.toml (tema nativo, sin HTML).
Datos: db.py (SQLite local o PostgreSQL).  Reglas del negocio: modelo.py.
"""
import hmac
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

import db
import modelo as md

AQUI = Path(__file__).resolve().parent
LOGO = AQUI / "logo.png"          # opcional: si pones el logo del cliente aquí, aparece solo
FRESA, HOJA = "#B5304F", "#2F6B4F"  # mismos colores del tema, para las gráficas


def secreto(clave, defecto=None):
    """Lee primero variables de entorno (Render, Railway...) y luego los secretos de Streamlit."""
    valor = os.environ.get(clave)
    if valor:
        return valor
    try:
        return st.secrets.get(clave, defecto)
    except Exception:
        return defecto


# ---------------------------------------------------------------- arranque
DB_URL = secreto("DATABASE_URL")
PASS_DUENO, PASS_CAJA = secreto("APP_PASSWORD"), secreto("CAJERO_PASSWORD")
md.TZ = secreto("TIMEZONE", "America/Guatemala")
db.configure(DB_URL)


@st.cache_resource(show_spinner=False)
def arrancar(url):
    db.init()
    return True


try:
    arrancar(DB_URL)
    A = md.ajustes()
except Exception as e:
    st.set_page_config(page_title="Sistema de gestión")
    st.error("No se pudo abrir la base de datos. Revisa DATABASE_URL en los secretos de la app.")
    st.caption(f"Detalle técnico: {type(e).__name__}")
    st.stop()

st.set_page_config(page_title=A["nombre"], page_icon=A["emoji"], layout="wide")
M = A["moneda"]
FMT = M + "%.2f"


def dinero(x):
    return f"{M}{x:,.2f}"


# ---------------------------------------------------------------- acceso
def pantalla_de_acceso():
    if PASS_DUENO is None:
        if DB_URL:
            st.error("Falta la contraseña. Define APP_PASSWORD en los secretos antes de publicar.")
            st.stop()
        st.session_state.rol = "dueno"  # prueba local sin contraseña
        return
    _, centro, _ = st.columns([1, 2, 1])
    with centro:
        st.title(f"{A['emoji']} {A['nombre']}")
        st.caption(A["eslogan"])
        with st.form("acceso"):
            clave = st.text_input("Contraseña", type="password")
            entrar = st.form_submit_button("Entrar", type="primary")
        if entrar:
            if hmac.compare_digest(clave.encode(), str(PASS_DUENO).encode()):
                st.session_state.rol = "dueno"
                st.rerun()
            elif PASS_CAJA and hmac.compare_digest(clave.encode(), str(PASS_CAJA).encode()):
                st.session_state.rol = "caja"
                st.rerun()
            time.sleep(1)
            st.error("Contraseña incorrecta.")
    st.stop()


def salir():
    st.session_state.pop("rol", None)
    st.session_state.cart = {}


if "rol" not in st.session_state:
    pantalla_de_acceso()
st.session_state.setdefault("cart", {})


# ---------------------------------------------------------------- piezas reutilizables
def editor_tabla(tabla, cols, config, req):
    """Tabla editable que guarda en la base de datos."""
    version = st.session_state.get("v", 0)
    orden = "orden, id" if "orden" in cols else "id"
    df = db.query(f"SELECT id, {', '.join(cols)} FROM {tabla} ORDER BY {orden}")
    if "activo" in df:
        df["activo"] = pd.to_numeric(df["activo"], errors="coerce").fillna(1).astype(bool)
    ed = st.data_editor(df, num_rows="dynamic", hide_index=True, column_config={"id": None, **config},
                        key=f"{tabla}{version}")
    if st.button("Guardar cambios", type="primary", key="g" + tabla, icon=":material/save:"):
        avisos = md.guardar_tabla(tabla, cols, df, ed, req)
        st.session_state.v = version + 1
        st.session_state.msg = " ".join(["Cambios guardados."] + avisos)
        st.rerun()


# ---------------------------------------------------------------- páginas
def inicio():
    hora = md.ahora().hour
    saludo = "Buenos días" if hora < 12 else "Buenas tardes" if hora < 19 else "Buenas noches"
    st.title(A["nombre"])
    st.caption(f"{saludo}. {A['eslogan']}")
    c, bajos = md.cfg(), md.bajos()
    if len(bajos):
        st.warning("Por reponer: " + ", ".join(f"{r.nombre} ({r.stock:g} {r.unidad})" for r in bajos.itertuples()),
                   icon=":material/inventory_2:")
    dias = st.segmented_control("Periodo", [7, 30, 60], default=30, label_visibility="collapsed",
                                format_func=lambda d: f"Últimos {d} días") or 30
    v = md.ventas(dias)
    if v.empty:
        st.info("Aún no hay ventas en este periodo. Registra la primera en **Vender**.")
        return
    fijo = md.gastos().monto.sum() * dias / 30
    ing, ub = v.ingreso.sum(), v.utilidad.sum()
    un = ub - fijo
    k = st.columns(4)
    k[0].metric("Ventas", dinero(ing), border=True)
    k[1].metric("Utilidad bruta", dinero(ub), f"{ub / ing * 100:.0f}% de margen" if ing else None, border=True)
    k[2].metric("Gastos fijos", dinero(fijo), border=True)
    k[3].metric("Utilidad neta", dinero(un), f"{un / ing * 100:.1f}% neto" if ing else None, border=True)
    st.caption(f"{int(v.cantidad.sum()):,} unidades vendidas. Los gastos fijos se reparten por día según el periodo.")

    izq, der = st.columns(2)
    with izq.container(border=True):
        st.subheader("Ventas y utilidad por día")
        st.line_chart(v.groupby("fecha")[["ingreso", "utilidad"]].sum(), color=[FRESA, HOJA])
    with der.container(border=True):
        st.subheader("Utilidad por categoría")
        st.bar_chart(v.groupby("categoria").utilidad.sum(), color=FRESA)

    r = v.groupby("etq").agg(unidades=("cantidad", "sum"), ingreso=("ingreso", "sum"), utilidad=("utilidad", "sum")).reset_index()
    r["margen_pct"] = r.utilidad / r.ingreso.where(r.ingreso > 0) * 100
    r["aporte_pct"] = r.utilidad / ub * 100 if ub else 0.0
    r = r.sort_values("utilidad", ascending=False)
    st.subheader("¿Cuánto deja cada producto?")
    st.dataframe(r, hide_index=True, column_config={
        "etq": "Producto", "unidades": "Unidades",
        "ingreso": st.column_config.NumberColumn("Ventas", format=FMT),
        "utilidad": st.column_config.NumberColumn("Utilidad", format=FMT),
        "margen_pct": st.column_config.NumberColumn("Margen", format="%.1f%%"),
        "aporte_pct": st.column_config.ProgressColumn("Aporte a la utilidad", format="%.0f%%", min_value=0, max_value=100)})
    top = r.iloc[0]
    st.info(f"Producto estrella: **{top.etq}**, con {top.aporte_pct:.0f}% de la utilidad.", icon=":material/star:")
    p = md.productos()
    flojos = p[p.activo & (p.margen_pct < c["meta"])]
    if len(flojos):
        st.warning(f"{len(flojos)} producto(s) están por debajo de tu meta de margen ({c['meta']:.0f}%). "
                   "Revisa la sección Productos.")


def _sumar(i):
    st.session_state.cart[i] = st.session_state.cart.get(i, 0) + 1


def _quitar(i):
    st.session_state.cart[i] -= 1
    if st.session_state.cart[i] <= 0:
        del st.session_state.cart[i]


def _cobrar(fecha):
    md.cobrar(st.session_state.cart, fecha)
    st.session_state.cart = {}
    st.session_state.msg = "Venta registrada"


def _deshacer():
    st.session_state.msg = md.deshacer()


def vender():
    st.title("Vender")
    p = md.productos()
    p = p[p.activo].copy()
    p["categoria"] = p.categoria.fillna("Otros")
    if p.empty:
        st.info("Agrega productos en la sección Productos.")
        return
    cart = {i: q for i, q in st.session_state.cart.items() if i in set(p.id)}  # ignora productos ya ocultos
    st.session_state.cart = cart
    idx = p.set_index("id")
    total = sum(idx.loc[i, "precio"] * q for i, q in cart.items())
    if cart:
        st.info(f"Pedido en curso: {sum(cart.values())} producto(s), {dinero(total)}. El resumen para cobrar está abajo.",
                icon=":material/receipt_long:")

    orden = [x.strip() for x in A["categorias"].split(",")]
    cats = [x for x in orden if x in set(p.categoria)] + sorted(set(p.categoria) - set(orden))
    izq, der = st.columns([3, 2], gap="large")
    with izq:
        for tab, cat in zip(st.tabs(cats), cats):
            with tab:
                grilla = st.columns(3)
                for n, r in enumerate(p[p.categoria == cat].itertuples()):
                    q = cart.get(int(r.id), 0)
                    texto = f"**{r.nombre}**  \n{r.tamano} · {M}{r.precio:,.0f}" + (f" · ×{q}" if q else "")
                    grilla[n % 3].button(texto, key=f"prod_{r.id}", on_click=_sumar, args=(int(r.id),),
                                         type="primary" if q else "secondary", width="stretch")
    with der, st.container(border=True):
        st.subheader("Pedido actual")
        for i, q in cart.items():
            st.button(f"{q} × {idx.loc[i, 'etq']} · {dinero(idx.loc[i, 'precio'] * q)}", key=f"q{i}",
                      icon=":material/remove:", on_click=_quitar, args=(i,), width="stretch")
        if not cart:
            st.caption("Toca un producto para agregarlo al pedido.")
        else:
            st.caption("Toca una línea para quitar una unidad.")
        st.metric("Total", dinero(total))
        fecha = st.date_input("Fecha de la venta", md.hoy())
        st.button("Cobrar", type="primary", disabled=not cart, on_click=_cobrar, args=(fecha,),
                  icon=":material/check:", width="stretch")
        st.button("Deshacer último pedido", on_click=_deshacer, icon=":material/undo:", width="stretch")
        h = md.ventas(1)
        st.caption(f"Hoy: {dinero(h.ingreso.sum())} en {int(h.cantidad.sum())} unidades")
    st.subheader("Últimos registros")
    st.dataframe(md.ultimos_registros(), hide_index=True,
                 column_config={"fecha": "Fecha", "producto": "Producto", "cantidad": "Cantidad"})


def productos():
    st.title("Productos, costos y precios")
    st.caption("Agrega productos en la fila vacía del final. Cambia **Orden** para moverlos, **Categoría** para pasarlos "
               "de sección, o desmarca **Activo** para ocultarlos de Vender sin borrarlos.")
    c = md.cfg()
    cats = [x.strip() for x in A["categorias"].split(",") if x.strip()]
    editor_tabla("productos", ["orden", "nombre", "categoria", "tamano", "precio", "costo_insumos", "costo_empaque", "activo"], {
        "orden": st.column_config.NumberColumn("Orden", step=1), "nombre": "Producto",
        "categoria": st.column_config.SelectboxColumn("Categoría", options=cats), "tamano": "Tamaño",
        "precio": st.column_config.NumberColumn("Precio de venta", format=FMT),
        "costo_insumos": st.column_config.NumberColumn("Costo de insumos", format=FMT),
        "costo_empaque": st.column_config.NumberColumn("Costo de empaque", format=FMT),
        "activo": st.column_config.CheckboxColumn("Activo", default=True)}, req="nombre")
    st.subheader("Margen por producto")
    p = md.productos()
    p["estado"] = p.margen_pct.apply(lambda z: "Saludable" if z >= c["meta"] else "Subir precio o bajar costo")
    st.dataframe(p[["etq", "precio", "costo_real", "margen_q", "margen_pct", "precio_sugerido", "estado"]], hide_index=True, column_config={
        "etq": "Producto", "precio": st.column_config.NumberColumn("Precio", format=FMT),
        "costo_real": st.column_config.NumberColumn("Costo real*", format=FMT),
        "margen_q": st.column_config.NumberColumn("Ganancia por unidad", format=FMT),
        "margen_pct": st.column_config.NumberColumn("Margen", format="%.1f%%"),
        "precio_sugerido": st.column_config.NumberColumn("Precio sugerido", format=FMT), "estado": "Estado"})
    st.caption("*Costo real = insumos + merma + empaque + comisiones e impuestos. El precio sugerido alcanza tu meta de margen.")


def inventario():
    st.title("Inventario")
    ins, bajos = md.insumos(), md.bajos()
    k = st.columns(3)
    k[0].metric("Insumos", len(ins), border=True)
    k[1].metric("Por reponer", len(bajos), border=True)
    k[2].metric("Valor en bodega", dinero((ins.stock * ins.costo).sum()), border=True)
    if len(bajos):
        st.warning("Por reponer: " + ", ".join(f"{r.nombre} ({r.stock:g} {r.unidad})" for r in bajos.itertuples()),
                   icon=":material/inventory_2:")
    t1, t2, t3 = st.tabs(["Existencias", "Compras y ajustes", "Recetas"])
    with t1:
        st.caption("El **mínimo** es la cantidad a la que avisamos que hay que comprar.")
        editor_tabla("insumos", ["nombre", "unidad", "stock", "minimo", "costo"], {
            "nombre": "Insumo", "unidad": "Unidad", "stock": "Existencia", "minimo": "Mínimo",
            "costo": st.column_config.NumberColumn("Costo por unidad", format=FMT)}, req="nombre")
    with t2:
        if ins.empty:
            st.info("Agrega insumos en Existencias.")
        else:
            nombres = dict(zip(ins.id.astype(int), ins.nombre))
            with st.form("movimiento", clear_on_submit=True):
                f = st.columns(3)
                iid = f[0].selectbox("Insumo", list(nombres), format_func=nombres.get)
                tipo = f[1].radio("Movimiento", ["Compra (entra)", "Merma o ajuste (sale)"])
                cant = f[2].number_input("Cantidad", min_value=0.0, value=1.0, step=1.0)
                if st.form_submit_button("Registrar movimiento", type="primary"):
                    md.mover_insumo(iid, cant if tipo.startswith("Compra") else -cant)
                    st.session_state.msg = "Inventario actualizado"
                    st.rerun()
    with t3:
        st.caption("Cuánto insumo gasta cada producto. Al cobrar una venta, el inventario baja solo.")
        prods = md.productos()
        pn, inn = dict(zip(prods.id.astype(int), prods.etq)), dict(zip(ins.id.astype(int), ins.nombre))
        rec = db.query("SELECT id, producto_id, insumo_id, cantidad FROM recetas ORDER BY producto_id, id")
        vista = pd.DataFrame({"id": rec.id, "producto": rec.producto_id.map(pn), "insumo": rec.insumo_id.map(inn),
                              "cantidad": rec.cantidad})
        ed = st.data_editor(vista, num_rows="dynamic", hide_index=True, key=f"recetas{st.session_state.get('v', 0)}", column_config={
            "id": None, "producto": st.column_config.SelectboxColumn("Producto", options=list(pn.values())),
            "insumo": st.column_config.SelectboxColumn("Insumo", options=list(inn.values())),
            "cantidad": "Cantidad por unidad"})
        if st.button("Guardar cambios", type="primary", key="grecetas", icon=":material/save:"):
            nuevo = pd.DataFrame({"id": ed["id"], "producto_id": ed["producto"].map({v: k for k, v in pn.items()}),
                                  "insumo_id": ed["insumo"].map({v: k for k, v in inn.items()}), "cantidad": ed["cantidad"]})
            md.guardar_tabla("recetas", ["producto_id", "insumo_id", "cantidad"], rec, nuevo, ["producto_id", "insumo_id"])
            st.session_state.v = st.session_state.get("v", 0) + 1
            st.session_state.msg = "Recetas guardadas"
            st.rerun()


def gastos():
    st.title("Gastos fijos y retribuciones")
    g = md.gastos()
    x = st.columns(3)
    x[0].metric("Total mensual", dinero(g.monto.sum()), border=True)
    x[1].metric("Por día", dinero(g.monto.sum() / 30), border=True)
    x[2].metric("Retribución del dueño", dinero(g[g.tipo == "Dueño"].monto.sum()), border=True)
    editor_tabla("gastos", ["concepto", "tipo", "monto"], {
        "concepto": "Concepto",
        "tipo": st.column_config.SelectboxColumn("Tipo", options=["Operativo", "Personal", "Dueño", "Marketing", "Otros"]),
        "monto": st.column_config.NumberColumn("Monto mensual", format=FMT)}, req="concepto")
    if not g.empty:
        st.bar_chart(g.groupby("tipo").monto.sum(), color=FRESA)


def metas():
    st.title("Punto de equilibrio y simulador")
    c, v, fijo = md.cfg(), md.ventas(30), md.gastos().monto.sum()
    u = v.cantidad.sum()
    if u == 0:
        st.info("Necesitas ventas de los últimos 30 días para calcular esto.")
        return
    ing = v.ingreso.sum()
    contribucion, precio_prom = v.utilidad.sum() / u, ing / u
    if contribucion <= 0:
        st.warning("Con los precios y costos actuales cada venta no deja ganancia. Revisa la sección Productos.")
        return
    n_eq = fijo / contribucion
    ing_eq = n_eq * precio_prom
    x = st.columns(4)
    x[0].metric("Unidades al mes para no perder", f"{n_eq:,.0f}", border=True)
    x[1].metric("Ventas al mes necesarias", dinero(ing_eq), border=True)
    x[2].metric("Unidades por día", f"{n_eq / 30:,.0f}", border=True)
    x[3].metric("Margen de seguridad", f"{(ing - ing_eq) / ing * 100:.0f}%", border=True)
    if ing_eq > 0:
        st.progress(min(ing / ing_eq, 1.0), text=f"Vas en {ing / ing_eq * 100:.0f}% de tu punto de equilibrio")
    st.subheader("¿Y si cambio algo?")
    s = st.columns(3)
    dp = s[0].slider("Precios %", -20, 30, 0)
    dv = s[1].slider("Volumen de ventas %", -30, 30, 0)
    dc = s[2].slider("Costo de insumos %", -20, 30, 0)
    pct = (c["comision"] + c["impuestos"]) / 100
    n_, pr = v.cantidad * (1 + dv / 100), v.precio * (1 + dp / 100)
    ing2 = (n_ * pr).sum()
    cv = (n_ * (v.costo_insumos * (1 + dc / 100) * (1 + c["merma"] / 100) + v.costo_empaque + pr * pct)).sum()
    neto1, neto2 = v.utilidad.sum() - fijo, ing2 - cv - fijo
    y = st.columns(2)
    y[0].metric("Utilidad neta actual (30 días)", dinero(neto1), border=True)
    y[1].metric("Utilidad neta simulada", dinero(neto2), dinero(neto2 - neto1), border=True)


def plan():
    st.title("Plan de mejora con tecnología")
    st.markdown("""
**Fase 1 · Primer mes: tener los números claros**
- Registrar cada venta en este sistema (30 segundos por pedido o un cierre al final del día).
- Subir precios en los productos marcados como "Subir precio o bajar costo" o reducir porciones y costos de insumos.
- Medir la merma de fresa a diario: la fruta que se pierde es dinero que no se ve.
- Fijar la receta exacta de cada bebida (gramos de matcha, leche, jarabe) para que el costo no cambie entre empleados.

**Fase 2 · Meses 2 a 3: vender más y desperdiciar menos**
- Inventario de insumos con alerta de compra mínima (fresa, matcha, leche, vasos).
- Combos de mayor margen: *Fresas + Matcha*. Las bebidas elevan el ticket promedio.
- Catálogo y pedidos por WhatsApp Business con mensajes automáticos.
- Menú con código QR y programa de puntos ("la 10.ª bebida gratis").

**Fase 3 · Meses 4 a 6: crecer con datos**
- Caja registradora en tablet con cobro por tarjeta y QR.
- Reporte semanal automático al correo del dueño (ventas, utilidad, alertas).
- Pronóstico de demanda por día para comprar la fruta justa.
- Respaldo en la nube y cuentas por rol (dueño ve costos, cajero solo vende).

**Indicadores que el dueño debe revisar cada semana**
Margen por producto · Ticket promedio · Merma % · Ventas vs punto de equilibrio · Utilidad neta.
""")


def ajustes():
    st.title("Ajustes")
    c = md.cfg()
    with st.container(border=True):
        st.subheader("Tu negocio")
        a = st.columns(2)
        nuevo = {"nombre": a[0].text_input("Nombre del negocio", A["nombre"]),
                 "eslogan": a[1].text_input("Frase de bienvenida", A["eslogan"]),
                 "emoji": a[0].text_input("Emoji del ícono", A["emoji"]),
                 "moneda": a[1].text_input("Símbolo de moneda", M),
                 "categorias": a[0].text_input("Categorías (separadas por coma)", A["categorias"])}
        if st.button("Guardar datos del negocio", type="primary"):
            md.guardar_ajustes(nuevo)
            st.session_state.msg = "Datos del negocio guardados"
            st.rerun()
        st.caption("Los colores y el logo se cambian en el diseño de la app (archivo .streamlit/config.toml y logo.png).")
    with st.container(border=True):
        st.subheader("Reglas del negocio")
        x = st.columns(4)
        reglas = {k: x[i].number_input(etq, 0.0, 100.0, float(c[k]), 0.5)
                  for i, (k, etq) in enumerate([("merma", "Merma de fruta %"), ("comision", "Comisión de tarjeta %"),
                                                ("impuestos", "Impuestos %"), ("meta", "Meta de margen %")])}
        if st.button("Guardar reglas", type="primary"):
            md.guardar_reglas(reglas)
            st.session_state.msg = "Reglas guardadas"
            st.rerun()
    with st.container(border=True):
        st.subheader("Datos y respaldo")
        if db.es_pg():
            st.caption("Base de datos en la nube (PostgreSQL). Los datos se conservan aunque la app se reinicie.")
        else:
            st.warning("Estás usando la base de datos local, solo para pruebas. En un hosting gratuito se borra al "
                       "reiniciar: configura DATABASE_URL antes de entregar la app.")
        st.download_button("Descargar respaldo en Excel", md.excel(), f"respaldo_{md.hoy().isoformat()}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:")
        st.caption("Ventas de ejemplo para enseñar el sistema. Se pueden borrar sin afectar tus ventas reales.")
        b = st.columns(2)
        if b[0].button("Cargar ventas de ejemplo", width="stretch"):
            db.cargar_demo(md.hoy())
            st.session_state.msg = "Ventas de ejemplo cargadas"
            st.rerun()
        if b[1].button("Borrar ventas de ejemplo", width="stretch"):
            db.borrar_demo()
            st.session_state.msg = "Ventas de ejemplo borradas"
            st.rerun()


# ---------------------------------------------------------------- navegación
TODAS = {
    "inicio": st.Page(inicio, title="Inicio", icon=":material/dashboard:", url_path="inicio", default=True),
    "vender": st.Page(vender, title="Vender", icon=":material/point_of_sale:", url_path="vender"),
    "productos": st.Page(productos, title="Productos", icon=":material/sell:", url_path="productos"),
    "inventario": st.Page(inventario, title="Inventario", icon=":material/inventory_2:", url_path="inventario"),
    "gastos": st.Page(gastos, title="Gastos", icon=":material/payments:", url_path="gastos"),
    "metas": st.Page(metas, title="Metas", icon=":material/flag:", url_path="metas"),
    "plan": st.Page(plan, title="Plan", icon=":material/rocket_launch:", url_path="plan"),
    "ajustes": st.Page(ajustes, title="Ajustes", icon=":material/settings:", url_path="ajustes"),
}
if st.session_state.rol == "caja":  # el cajero solo ve la caja: no ve costos ni utilidades
    paginas = [st.Page(vender, title="Vender", icon=":material/point_of_sale:", url_path="vender", default=True)]
else:
    paginas = list(TODAS.values())

if LOGO.exists():
    st.logo(str(LOGO))
pg = st.navigation(paginas)
if PASS_DUENO is not None:
    st.sidebar.button("Cerrar sesión", icon=":material/logout:", on_click=salir, width="stretch")
if "msg" in st.session_state:
    st.toast(st.session_state.pop("msg"), icon=":material/check_circle:")
pg.run()
