from flask import Flask, request, send_file,  render_template, flash, redirect, url_for
import pdfplumber
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import re
import io

# -------------------------------
# Funciones de conversión y utilidades
# -------------------------------

def wrap_text(text, max_width, c, font_name, font_size):
    """Envuelve el texto para que quepa en max_width usando stringWidth de ReportLab."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + (" " if current_line else "") + word
        if c.stringWidth(test_line, font_name, font_size) <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines

def extraer_items(lines):
    """
    Extrae los items a partir del bloque de la tabla.
    Cada item se espera que inicie con la cantidad (número decimal),
    seguido de tokens y que, al final de la descripción, pueda venir un token que sea el valor unitario.
    """
    items = []
    current_item = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r'^\d+\.\d+', line):
            if current_item is not None:
                items.append(current_item)
            tokens = line.split()
            if len(tokens) < 4:
                continue
            cantidad = tokens[0]
            token3 = tokens[2]
            m = re.match(r'^(\d+\.\d+)', token3)
            valor_unitario = m.group(1) if m else ""
            descripcion = " ".join(tokens[3:])
            desc_tokens = descripcion.split()
            if desc_tokens and re.match(r'^\d+\.\d+$', desc_tokens[-1]):
                valor_unitario = desc_tokens.pop()
                descripcion = " ".join(desc_tokens)
            current_item = {
                "cantidad": cantidad,
                "valor_unitario": valor_unitario,
                "descripcion": descripcion
            }
        else:
            if current_item is not None:
                desc_tokens = line.split()
                if desc_tokens and re.match(r'^\d+\.\d+$', desc_tokens[-1]):
                    price_candidate = desc_tokens.pop()
                    line = " ".join(desc_tokens)
                    current_item["valor_unitario"] = price_candidate
                current_item["descripcion"] += " " + line
    if current_item is not None:
        items.append(current_item)
    return items

def limpiar_header_line(line):
    """
    Limpia una línea del encabezado:
      - Si contiene la palabra "BOLETA", devuelve el texto anterior a ella.
      - Si la línea empieza con "RUC", se descarta.
      - Si contiene un patrón tipo "EB" seguido de números, se elimina ese patrón y lo que sigue.
      - En otro caso, retorna la línea.
    """
    up_line = line.upper()
    if "BOLETA" in up_line:
        index = up_line.find("BOLETA")
        return line[:index].strip()
    if up_line.startswith("RUC"):
        return ""
    m = re.search(r'(EB\d+\s*[-–]\s*\d+)', up_line)
    if m:
        return line[:m.start()].strip()
    return line.strip()

def extraer_datos_boleta(texto):
    """
    Extrae los datos clave del PDF de boleta.
    
    Para el encabezado se toman las primeras 4 líneas del texto original y se limpian con la función
    limpiar_header_line. Se asigna:
      - Línea 1: Nombre Comercial.
      - Línea 2: Razón Social.
      - Líneas 3 y 4 (si existen): Dirección.
    Los demás campos se extraen mediante expresiones regulares.
    """
    datos = {
        "nombre_comercial": "",
        "razon_social": "",
        "direccion": "",
        "ruc": "",
        "fecha_emision": "",
        "numero_doc": "",
        "cliente": "",
        "doc_cliente": "",
        "tipo_moneda": "",
        "items": [],
        "subtotal": "",
        "total": "",
    }
    lines = [line.strip() for line in texto.splitlines() if line.strip()]
    # Tomamos las primeras 4 líneas y las limpiamos
    header = []
    for l in lines[:6]:
        limpio = limpiar_header_line(l)
        if limpio:
            header.append(limpio)
    if header:
        datos["nombre_comercial"] = header[0]
    if len(header) >= 2:
        datos["razon_social"] = header[1]
    if len(header) >= 3:
        datos["direccion"] = "\n".join(header[2:])
    
    # RUC
    ruc_match = re.search(r'RUC\s*:\s*(\d+)', texto, re.IGNORECASE)
    datos["ruc"] = ruc_match.group(1) if ruc_match else ""
    # Fecha de Emisión
    fecha_match = re.search(r'Fecha\s*de\s*Emisi[oó]n\s*:\s*([\d/]+)', texto, re.IGNORECASE)
    datos["fecha_emision"] = fecha_match.group(1) if fecha_match else ""
    # Número de Boleta
    doc_match = re.search(r'(EB\d+\s*[-–]\s*\d+)', texto, re.IGNORECASE)
    datos["numero_doc"] = doc_match.group(1) if doc_match else ""
    # Cliente: buscar "Señor (es):"
    m_cliente = re.search(r'Señor\s*\(es\)\s*:\s*(.+)', texto, re.IGNORECASE)
    if m_cliente:
        candidate = m_cliente.group(1).strip()
        datos["cliente"] = candidate if candidate.lower() != "null" and candidate != "" else "Clientes Varios"
    else:
        datos["cliente"] = "Clientes Varios"
    # Documento del Cliente
    m_doc = re.search(r'(DNI|SIN DOCUMENTO)\s*:\s*([\w-]+)', texto, re.IGNORECASE)
    datos["doc_cliente"] = m_doc.group(2).strip() if m_doc else ""
    # Tipo de Moneda
    m_moneda = re.search(r'Tipo\s*de\s*Moneda\s*:\s*(\S+)', texto, re.IGNORECASE)
    datos["tipo_moneda"] = m_moneda.group(1).strip() if m_moneda else ""
    # Bloque de items
    header_keywords = ["Cantidad", "Unidad Medida", "Código", "Valor Unitario", "Descripción"]
    header_index = None
    for i, line in enumerate(lines):
        if all(word in line for word in header_keywords):
            header_index = i
            break
    if header_index is not None:
        items_lines = []
        for line in lines[header_index+1:]:
            if re.search(r'(Sub\s*Total|Importe Total)', line, re.IGNORECASE):
                break
            items_lines.append(line)
        datos["items"] = extraer_items(items_lines)
    else:
        datos["items"] = []
    # Subtotal y Total
    subtotal_match = re.search(r'Sub\s*Total\s*Ventas?\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["subtotal"] = subtotal_match.group(1) if subtotal_match else ""
    total_match = re.search(r'Importe\s*Total\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["total"] = total_match.group(1) if total_match else ""
    return datos

def calcular_altura(datos, c):
    """
    Calcula la altura total necesaria para el contenido, usando los mismos parámetros de diseño.
    """
    total = 5 * mm  # margen superior
    # Encabezado - Nombre Comercial
    if datos["nombre_comercial"]:
        font_size = 20
        wrapped = wrap_text(datos["nombre_comercial"], 52 * mm, c, "Helvetica-Bold", font_size)
        total += len(wrapped) * (font_size + 2)
    # Razón Social
    if datos["razon_social"]:
        total += len(wrap_text(datos["razon_social"], 52 * mm, c, "Helvetica", 12)) * (12 + 2)
    # Dirección
    if datos["direccion"]:
        total += len(wrap_text(datos["direccion"], 52 * mm, c, "Helvetica", 10)) * (10 + 2)
    total += 5 * mm  # espacio extra
    
    # Bloque de Identificación
    total += len(wrap_text("BOLETA ELECTRÓNICA", 48 * mm, c, "Helvetica-Bold", 12)) * 12
    fields = [f"RUC: {datos.get('ruc', '')}",
              datos.get("numero_doc", ""),
              f"Fecha de Emisión: {datos.get('fecha_emision', '')}",
              f"Señor (es): {datos.get('cliente', 'Clientes Varios')}"]
    if datos.get("doc_cliente"):
        fields.append(f"DNI: {datos.get('doc_cliente')}")
    if datos.get("tipo_moneda"):
        fields.append(f"Tipo de Moneda: {datos.get('tipo_moneda')}")
    for field in fields:
        total += len(wrap_text(field, 48 * mm, c, "Helvetica", 10)) * 10
    total += 10  # línea divisoria y margen
    # Tabla de items
    total += 12  # tabla header
    for item in datos["items"]:
        wrapped_desc = wrap_text(item.get("descripcion", ""), 36 * mm, c, "Helvetica", 7)
        total += len(wrapped_desc) * 8 + 2
    total += 5  # margen abajo de tabla
    total += 10  # línea divisoria
    total += 12 + 12 + 14 + 10  # Totales y mensaje final
    return total

def generar_pdf_58mm(datos, nombre_salida):
    """
    Genera un PDF de 58 mm de ancho con la siguiente estructura:
    
      Encabezado:
        - Fila 1: Nombre Comercial (inicia en 30 pt y se reduce solo si no cabe).
        - Fila 2: Razón Social (14 pt).
        - Fila 3 (Dirección, si existe): 12 pt.
      
      Bloque de Identificación:
        - "BOLETA ELECTRÓNICA" (10 pt, negrita).
        - RUC, Número, Fecha, Cliente, etc. en 9 pt.
      
      Tabla de Items y Totales:
        - Columnas 1 y 3 estrechas (8 mm) y columna central (36 mm) para la descripción.
        - Encabezado de la tabla en 8 pt (negrita), contenido en 7 pt.
    """
    ancho_hoja = 58 * mm
    alto_hoja = 300 * mm
    c = canvas.Canvas(nombre_salida, pagesize=(ancho_hoja, alto_hoja))
    
    left_margin = 3 * mm
    effective_width = ancho_hoja - 2 * left_margin  # 52 mm de ancho central
    center_x = left_margin + effective_width / 2
    y = alto_hoja - 5 * mm

    #
    # ENCABEZADO
    #
    if datos["nombre_comercial"]:
        font_size = 12
        while (c.stringWidth(datos["nombre_comercial"], "Helvetica-Bold", font_size) > effective_width) and (font_size > 12):
            font_size -= 1
        c.setFont("Helvetica-Bold", font_size)
        wrapped_nombre = wrap_text(datos["nombre_comercial"], effective_width, c, "Helvetica-Bold", font_size)
        for linea in wrapped_nombre:
            c.drawCentredString(center_x, y, linea)
            y -= (font_size + 2)

    if datos["razon_social"]:
        c.setFont("Helvetica", 7)
        wrapped_rs = wrap_text(datos["razon_social"], effective_width, c, "Helvetica", 7)
        for linea in wrapped_rs:
            c.drawCentredString(center_x, y, linea)
            y -= 7 
    y -= 4
    if datos["direccion"]:
        lines = datos["direccion"].splitlines()
        unique_lines = []
        for l in lines:
            if l not in unique_lines:
                unique_lines.append(l)
        def remove_line_duplication(line):
            parts = line.split(" - ")
            new_parts = []
            for part in parts:
                if not new_parts or part != new_parts[-1]:
                    new_parts.append(part)
            return " - ".join(new_parts)
        clean_lines = [remove_line_duplication(l) for l in unique_lines]
        direccion_limpia = " ".join(clean_lines)
        c.setFont("Helvetica", 7)
        wrapped_addr = wrap_text(direccion_limpia, effective_width, c, "Helvetica", 7)
        for linea in wrapped_addr:
            c.drawCentredString(center_x, y, linea)
            y -= 7

    y -= 10

    #
    # BLOQUE DE IDENTIFICACIÓN
    #
    id_max_width = ancho_hoja - 6 * mm
    c.setFont("Helvetica-Bold", 10)
    wrapped_title = wrap_text("BOLETA ELECTRÓNICA", id_max_width, c, "Helvetica-Bold", 10)
    for linea in wrapped_title:
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 10

    c.setFont("Helvetica", 8)
    for linea in wrap_text(f"RUC: {datos.get('ruc', '')}", id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    for linea in wrap_text(datos.get("numero_doc", ""), id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    for linea in wrap_text(f"Fecha de Emisión: {datos.get('fecha_emision', '')}", id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    for linea in wrap_text(f"Señor (es): {datos.get('cliente', 'Clientes Varios')}", id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    if datos.get("doc_cliente"):
        for linea in wrap_text(f"DNI: {datos.get('doc_cliente')}", id_max_width, c, "Helvetica", 9):
            c.drawCentredString(ancho_hoja/2, y, linea)
            y -= 9
    if datos.get("tipo_moneda"):
        for linea in wrap_text(f"Tipo de Moneda: {datos.get('tipo_moneda')}", id_max_width, c, "Helvetica", 9):
            c.drawCentredString(ancho_hoja/2, y, linea)
            y -= 9

    y -= 10
    c.line(left_margin, y, ancho_hoja - left_margin, y)
    y -= 10

    #
    # TABLA DE ITEMS
    #
    col1_width = 8 * mm
    col3_width = 8 * mm
    col2_width = ancho_hoja - (left_margin + col1_width + col3_width + left_margin)
    col1_x = left_margin
    col2_x = col1_x + col1_width
    col3_x = col2_x + col2_width
    pad_col1 = 1
    pad_col2 = 2
    pad_col3 = 1
    col2_internal_width = col2_width - (2 * pad_col2)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(col1_x + pad_col1, y, "Cant")
    c.drawString(col2_x + pad_col2, y, "Descripción")
    c.drawRightString(col3_x + col3_width - pad_col3, y, "Valor")
    y -= 12

    c.setFont("Helvetica", 7)
    line_height = 8
    for item in datos["items"]:
        cantidad = item.get("cantidad", "")
        valor_unitario = item.get("valor_unitario", "")
        descripcion = item.get("descripcion", "")
        wrapped_desc = wrap_text(descripcion, col2_internal_width, c, "Helvetica", 7)
        num_lines = len(wrapped_desc)
        row_height = num_lines * line_height
        c.drawString(col1_x + pad_col1, y, cantidad)
        for i, linea in enumerate(wrapped_desc):
            c.drawString(col2_x + pad_col2, y - (i * line_height), linea)
        c.drawRightString(col3_x + col3_width - pad_col3, y, f"S/ {valor_unitario}")
        y -= row_height + 2

    y -= 5
    c.line(left_margin, y, ancho_hoja - left_margin, y)
    y -= 10

    c.setFont("Helvetica-Bold", 8)
    c.drawString(left_margin, y, f"Subtotal: S/ {datos.get('subtotal', '')}")
    y -= 12
    c.drawString(left_margin, y, f"Total: S/ {datos.get('total', '')}")
    y -= 14

    c.setFont("Helvetica", 7)
    c.drawCentredString(ancho_hoja/2, y, "Gracias por su compra")
    y -= 10

    c.showPage()
    c.save()

def convertir_boleta_sunat_58mm(pdf_entrada, pdf_salida):
    try:
        with pdfplumber.open(pdf_entrada) as pdf:
            texto_completo = ""
            for page in pdf.pages:
                texto_completo += page.extract_text() + "\n"
        datos = extraer_datos_boleta(texto_completo)
        generar_pdf_58mm(datos, pdf_salida)
        return True, "Conversión exitosa"
    except Exception as e:
        return False, str(e)

def convertir_boleta_sunat_58mm_flask(file_storage):
    try:
        with pdfplumber.open(file_storage) as pdf:
            texto_completo = ""
            for page in pdf.pages:
                texto_completo += page.extract_text() + "\n"
        datos = extraer_datos_boleta(texto_completo)
        output_pdf = io.BytesIO()
        generar_pdf_58mm(datos, output_pdf)
        output_pdf.seek(0)
        return True, output_pdf
    except Exception as e:
        return False, str(e)

# -------------------------------
# Aplicación Flask
# -------------------------------

app = Flask(__name__)
app.secret_key = 'una_clave_secreta'

template = '''
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Conversor de Boleta a 58mm</title>
</head>
<body>
  <h1>Conversor de Boleta a 58mm</h1>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <ul style="color:red;">
      {% for message in messages %}
        <li>{{ message }}</li>
      {% endfor %}
      </ul>
    {% endif %}
  {% endwith %}
  <form method="post" enctype="multipart/form-data">
    <label for="pdf_file">Selecciona el archivo PDF de la boleta:</label><br>
    <input type="file" name="pdf_file" id="pdf_file" accept=".pdf" required><br><br>
    <button type="submit">Convertir</button>
  </form>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'pdf_file' not in request.files:
            flash('No se encontró el archivo PDF.')
            return redirect(url_for('index'))
        file = request.files['pdf_file']
        if file.filename == '':
            flash('No se seleccionó ningún archivo.')
            return redirect(url_for('index'))
        success, result = convertir_boleta_sunat_58mm_flask(file)
        if success:
            return send_file(result,
                                as_attachment=True,
                                download_name="boleta_58mm.pdf",
                                mimetype='application/pdf')
        else:
            flash("Error al convertir: " + result)
            return redirect(url_for('index'))
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
